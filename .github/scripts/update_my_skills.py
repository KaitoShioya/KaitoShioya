#!/usr/bin/env python3
# .github/scripts/update_my_skills.py
"""
Generates categorized skill badges and injects into README between markers:
<!-- SKILLS-START --> and <!-- SKILLS-END -->

Behavior:
- Collect repos (from REPO_LIST or list user repos)
- Detect languages/tools/services/databases via file-tree + file-content heuristics
- Map detections into 6 categories:
  Programming languages, Frontend development, Misc tools, Services & Frameworks, Databases, DevOps
- Create shields.io badge URLs of the form:
  https://img.shields.io/badge/-{skill name}-000?&logo={skill name}
- Only include a badge if a HEAD request to that URL returns HTTP 200.
- Exclude package managers from final badges.
"""
import os
import requests
import time
import urllib.parse
from collections import Counter, defaultdict

GITHUB_API = "https://api.github.com"
OWNER = os.getenv("OWNER")
REPO = os.getenv("REPO")
ACCESS_TOKEN = os.getenv("ACCESS_TOKEN")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
REPO_LIST = os.getenv("REPO_LIST")  # optional: "owner/repo,owner2/repo2"

HEADERS = {}
if ACCESS_TOKEN:
    HEADERS["Authorization"] = f"token {ACCESS_TOKEN}"
elif GITHUB_TOKEN:
    HEADERS["Authorization"] = f"token {GITHUB_TOKEN}"
HEADERS["Accept"] = "application/vnd.github.v3+json"

REQUEST_TIMEOUT = 30

# --- Utility functions for GitHub API ---
def api_get(path, params=None):
    url = f"{GITHUB_API}{path}"
    r = requests.get(url, headers=HEADERS, params=params, timeout=REQUEST_TIMEOUT)
    r.raise_for_status()
    return r.json()

def list_all_repos():
    if REPO_LIST:
        return [r.strip() for r in REPO_LIST.split(",") if r.strip()]
    repos = []
    page = 1
    # try listing user repos first
    while True:
        try:
            data = api_get(f"/users/{OWNER}/repos", params={"per_page": 100, "page": page, "type": "all"})
        except requests.HTTPError:
            break
        if not data:
            break
        repos.extend([f"{r['owner']['login']}/{r['name']}" for r in data])
        if len(data) < 100:
            break
        page += 1
    return repos

def get_repo_default_branch(owner, repo):
    data = api_get(f"/repos/{owner}/{repo}")
    return data.get("default_branch", "main")

def get_tree(owner, repo, branch):
    try:
        return api_get(f"/repos/{owner}/{repo}/git/trees/{branch}", params={"recursive": "1"})
    except requests.HTTPError:
        return {"tree": []}

def get_file_content(owner, repo, path):
    # returns raw content (decoded) or None
    try:
        r = requests.get(f"{GITHUB_API}/repos/{owner}/{repo}/contents/{urllib.parse.quote(path, safe='')}",
                         headers=HEADERS, timeout=REQUEST_TIMEOUT)
        if r.status_code == 200:
            j = r.json()
            if j.get("encoding") == "base64" and "content" in j:
                import base64
                return base64.b64decode(j["content"]).decode("utf-8", errors="ignore")
            else:
                return j.get("content", "")
        else:
            return None
    except Exception:
        return None

# --- Detection heuristics ---
# files detection -> tentative tools
FILE_TOOL_MAP = {
    "package.json": ["nodejs"],
    "pyproject.toml": ["python"],
    "requirements.txt": ["python"],
    "Pipfile": ["python"],
    "poetry.lock": ["python"],
    "go.mod": ["go"],
    "pom.xml": ["java"],
    "build.gradle": ["java"],
    "Gemfile": ["ruby"],
    "composer.json": ["php"],
    "Dockerfile": ["docker"],
    "next.config.js": ["next.js"],
    "nuxt.config": ["nuxt"],
    "angular.json": ["angular"],
    "vite.config.js": ["vite"],
    "webpack.config.js": ["webpack"],
    "Cargo.toml": ["rust"],
    "pom.xml": ["maven"],
    "terraform.tf": ["terraform"],
}

# package manager names to exclude from final badges
PACKAGE_MANAGERS = {
    "npm", "yarn", "pip", "pipenv", "poetry", "composer", "cargo", "bundler", "gem"
}

# DB keyword map: keyword -> normalized label
DB_KEYWORDS = {
    "postgres": "PostgreSQL",
    "postgresql": "PostgreSQL",
    "psycopg2": "PostgreSQL",
    "pg": "PostgreSQL",
    "mysql": "MySQL",
    "mariadb": "MariaDB",
    "pymysql": "MySQL",
    "mysql-connector": "MySQL",
    "sqlite": "SQLite",
    "sqlite3": "SQLite",
    "mongodb": "MongoDB",
    "mongo": "MongoDB",
    "redis": "Redis",
    "cockroach": "CockroachDB",
    "mssql": "SQL Server",
    "sqlserver": "SQL Server",
    "azure-sqldb": "SQL Server",
    # add others as needed
}

# Frontend frameworks
FRONTEND_KEYWORDS = {
    "react": "React",
    "next": "Next.js",
    "next.js": "Next.js",
    "vue": "Vue.js",
    "nuxt": "Nuxt",
    "angular": "Angular",
    "svelte": "Svelte",
    "vite": "Vite",
    "webpack": "Webpack",
}

# Services & frameworks (backend, libs)
SERVICE_KEYWORDS = {
    "django": "Django",
    "flask": "Flask",
    "fastapi": "FastAPI",
    "express": "Express",
    "spring": "Spring",
    "spring-boot": "Spring Boot",
    "laravel": "Laravel",
    "asp.net": "ASP.NET",
    "gin": "Gin",
    "grpc": "gRPC",
    "graphql": "GraphQL",
    "rabbitmq": "RabbitMQ",
    "kafka": "Kafka",
}

# DevOps / infra
DEVOPS_KEYWORDS = {
    "docker": "Docker",
    "kubernetes": "Kubernetes",
    "k8s": "Kubernetes",
    "helm": "Helm",
    "terraform": "Terraform",
    "ansible": "Ansible",
    "github actions": "GitHub Actions",
    "github-actions": "GitHub Actions",
    "circleci": "CircleCI",
    "gitlab-ci": "GitLab CI",
    "travis": "Travis CI",
}

# Programming languages mapping (GitHub languages usually suffice)
LANGUAGE_NORMALIZE = {
    "JavaScript": "JavaScript",
    "TypeScript": "TypeScript",
    "Python": "Python",
    "Go": "Go",
    "Java": "Java",
    "C#": "C#",
    "C++": "C++",
    "Ruby": "Ruby",
    "PHP": "PHP",
    "Rust": "Rust",
    "Shell": "Shell",
    "HTML": "HTML",
    "CSS": "CSS",
}

# category containers
category_map = {
    "Programming languages": Counter(),
    "Frontend development": Counter(),
    "Misc tools": Counter(),
    "Services & Frameworks": Counter(),
    "Databases": Counter(),
    "DevOps": Counter(),
}

# helper to add to category
def add_skill(category, name):
    if name in PACKAGE_MANAGERS:
        return
    category_map[category][name] += 1

# check shields image existence
def badge_url_for(skill_name):
    # use the raw skill name verbatim in label/logo param as requested
    # but url encode accordingly
    label = urllib.parse.quote(f"-{skill_name}")
    logo = urllib.parse.quote(skill_name)
    # black background text 000 and style default for-the-badge to be consistent
    return f"https://img.shields.io/badge/{label}-000?&logo={logo}"

def badge_exists(url):
    try:
        # HEAD is lighter; some servers do not accept HEAD — fallback to GET with small timeout
        r = requests.head(url, timeout=10)
        if r.status_code == 200:
            return True
        # fallback
        r = requests.get(url, timeout=10)
        return r.status_code == 200
    except Exception:
        return False

# content scanning utilities
def scan_file_text_for_keywords(text, keywords_map):
    found = set()
    txt = (text or "").lower()
    for k, label in keywords_map.items():
        if k.lower() in txt:
            found.add(label)
    return found

def detect_for_repo(full):
    owner, r = full.split("/", 1)
    branch = get_repo_default_branch(owner, r)
    tree = get_tree(owner, r, branch)
    paths = [e["path"] for e in tree.get("tree", []) if e.get("type") == "blob"]
    detected = {
        "languages": {},
        "frontend": set(),
        "services": set(),
        "dbs": set(),
        "devops": set(),
        "misc": set(),
    }
    # languages via API
    try:
        langs = api_get(f"/repos/{owner}/{r}/languages")
        for L in langs.keys():
            if L in LANGUAGE_NORMALIZE:
                detected["languages"][LANGUAGE_NORMALIZE[L]] = langs[L]
            else:
                detected["languages"][L] = langs[L]
    except Exception:
        pass

    # filename-based heuristics + content scanning
    # check for specific file names quickly
    candidates_to_fetch = []
    for p in paths:
        basename = os.path.basename(p).lower()
        if basename in FILE_TOOL_MAP:
            for t in FILE_TOOL_MAP[basename]:
                # map some to categories later
                if t == "docker":
                    detected["devops"].add("Docker")
                elif t == "terraform":
                    detected["devops"].add("Terraform")
                elif t == "go":
                    detected["languages"]["Go"] = detected["languages"].get("Go", 0)
                # mark for further inspect
            # but also fetch file to look at dependencies
            candidates_to_fetch.append(p)
        # detect tf files
        if p.endswith(".tf"):
            detected["devops"].add("Terraform")
            candidates_to_fetch.append(p)
        if p.endswith(".sql"):
            candidates_to_fetch.append(p)
        if "dockerfile" in basename:
            detected["devops"].add("Docker")
            candidates_to_fetch.append(p)
        # env files
        if basename in {".env", ".env.example", ".env.sample", "database.yml", "database.yaml", "application.yml", "config.yml"}:
            candidates_to_fetch.append(p)
        # common package manifests to inspect dependencies
        if basename in {"package.json", "requirements.txt", "pyproject.toml", "pom.xml", "go.mod", "composer.json", "Gemfile", "Cargo.toml"}:
            candidates_to_fetch.append(p)

    # fetch the candidate files and scan text
    content_blob = ""
    for path in set(candidates_to_fetch):
        txt = get_file_content(owner, r, path)
        if txt:
            content_blob += "\n" + txt.lower()
        # short sleep to avoid rate-limit
        time.sleep(0.1)

    # Database detection via keywords in files
    dbs_found = scan_file_text_for_keywords(content_blob, DB_KEYWORDS)
    for d in dbs_found:
        detected["dbs"].add(d)

    # Frontend frameworks detection
    fe_found = scan_file_text_for_keywords(content_blob, FRONTEND_KEYWORDS)
    for f in fe_found:
        detected["frontend"].add(f)

    # Services & frameworks detection
    svc_found = scan_file_text_for_keywords(content_blob, SERVICE_KEYWORDS)
    for s in svc_found:
        detected["services"].add(s)

    # DevOps detection
    dev_found = scan_file_text_for_keywords(content_blob, DEVOPS_KEYWORDS)
    for d in dev_found:
        detected["devops"].add(d)

    # Misc: detect some other tools (git, celery, redis client libs etc.)
    misc_candidates = []
    # search for 'redis' also put into db category if present (Redis can be a DB)
    if "redis" in content_blob:
        detected["dbs"].add("Redis")
    if "graphql" in content_blob:
        detected["services"].add("GraphQL")
    if "kubernetes" in content_blob or "k8s" in content_blob:
        detected["devops"].add("Kubernetes")
    if "github actions" in content_blob or ".github/workflows" in "\n".join(paths):
        detected["devops"].add("GitHub Actions")

    # fallback: often package.json lists dependencies like next/react/express
    # attempt to parse package.json content if present (quick parse)
    if "package.json" in (p.lower() for p in paths):
        raw = get_file_content(owner, r, "package.json")
        if raw:
            try:
                import json
                pj = json.loads(raw)
                deps = {}
                deps.update(pj.get("dependencies", {}))
                deps.update(pj.get("devDependencies", {}))
                dep_keys = " ".join(deps.keys()).lower()
                # scan keys
                fe_found2 = scan_file_text_for_keywords(dep_keys, FRONTEND_KEYWORDS)
                for f in fe_found2:
                    detected["frontend"].add(f)
                svc_found2 = scan_file_text_for_keywords(dep_keys, SERVICE_KEYWORDS)
                for s in svc_found2:
                    detected["services"].add(s)
                # db libs
                db_found2 = scan_file_text_for_keywords(dep_keys, DB_KEYWORDS)
                for d in db_found2:
                    detected["dbs"].add(d)
            except Exception:
                pass

    return detected

# MAIN routine
repos = list_all_repos()
if not repos:
    print("No repos found; exiting.")
    exit(0)

aggregate = {
    "languages": Counter(),
    "frontend": Counter(),
    "services": Counter(),
    "dbs": Counter(),
    "devops": Counter(),
    "misc": Counter(),
}

# iterate repos and accumulate detections
for full in repos:
    try:
        det = detect_for_repo(full)
    except Exception as e:
        print(f"Error detecting {full}: {e}")
        continue
    # languages
    for lang in det.get("languages", {}).keys():
        aggregate["languages"][lang] += 1
    for f in det.get("frontend", []):
        aggregate["frontend"][f] += 1
    for s in det.get("services", []):
        aggregate["services"][s] += 1
    for d in det.get("dbs", []):
        aggregate["dbs"][d] += 1
    for dv in det.get("devops", []):
        aggregate["devops"][dv] += 1
    # misc can be augmented later

# map aggregates into categories and filter out package managers
for lang, cnt in aggregate["languages"].most_common():
    if lang.lower() in PACKAGE_MANAGERS:
        continue
    add_skill("Programming languages", lang)

# Frontend
for f, cnt in aggregate["frontend"].most_common():
    add_skill("Frontend development", f)

# Services & Frameworks
for s, cnt in aggregate["services"].most_common():
    add_skill("Services & Frameworks", s)

# Databases (use normalized names)
for d, cnt in aggregate["dbs"].most_common():
    add_skill("Databases", d)

# DevOps
for dv, cnt in aggregate["devops"].most_common():
    add_skill("DevOps", dv)

# Misc tools: include detections not categorized (for now, empty or future extension)
# (We intentionally exclude package managers)
# Build final markdown with headings (###) and badges
def build_badge_md(skill_name):
    url = badge_url_for(skill_name)
    if badge_exists(url):
        return f"![{skill_name}]({url})"
    else:
        return None

sections = []
order_of_categories = [
    "Programming languages",
    "Frontend development",
    "Misc tools",
    "Services & Frameworks",
    "Databases",
    "DevOps"
]

for cat in order_of_categories:
    counter = category_map[cat]
    if not counter:
        continue
    # sort by frequency desc
    items = [k for k, _ in counter.most_common()]
    badges = []
    for it in items:
        md = build_badge_md(it)
        if md:
            badges.append(md)
    if badges:
        sections.append(f"### {cat}\n\n{' '.join(badges)}\n")

if not sections:
    new_section = "### My Skills\n\n_No detected skills._\n"
else:
    new_section = "\n\n".join(sections)

# Insert into README
README_PATH = "README.md"
START = "<!-- SKILLS-START -->"
END = "<!-- SKILLS-END -->"

if not os.path.exists(README_PATH):
    print("README.md not found. Creating new README.md with skills section.")
    with open(README_PATH, "w", encoding="utf-8") as f:
        f.write(START + "\n" + new_section + "\n" + END + "\n")
else:
    with open(README_PATH, "r", encoding="utf-8") as f:
        text = f.read()
    if START in text and END in text:
        before, rest = text.split(START, 1)
        _, after = rest.split(END, 1)
        new_text = before + START + "\n" + new_section + "\n" + END + after
    else:
        new_text = text + "\n\n" + START + "\n" + new_section + "\n" + END + "\n"
    with open(README_PATH, "w", encoding="utf-8") as f:
        f.write(new_text)

# commit & push
import subprocess, sys
try:
    subprocess.run(["git", "config", "user.name", "github-actions"], check=True)
    subprocess.run(["git", "config", "user.email", "github-actions@users.noreply.github.com"], check=True)
    subprocess.run(["git", "add", README_PATH], check=True)
    subprocess.run(["git", "commit", "-m", "chore: update categorized My Skills badges [skip ci]"], check=True)
    push_token = ACCESS_TOKEN or GITHUB_TOKEN
    if not push_token:
        print("No token available to push changes. Please set ACCESS_TOKEN or rely on GITHUB_TOKEN.")
        sys.exit(0)
    # Determine branch to push to: prefer current branch env var, fallback to default 'main'
    branch = os.getenv("GITHUB_REF_NAME") or "main"
    repo_url = f"https://{push_token}@github.com/{OWNER}/{REPO}.git"
    subprocess.run(["git", "push", repo_url, f"HEAD:refs/heads/{branch}"], check=True)
except subprocess.CalledProcessError as e:
    print("Git error (commit/push):", e)
