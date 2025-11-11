#!/usr/bin/env python3
# .github/scripts/update_my_skills.py
"""
Generate categorized skill badges for README from all repositories
discoverable by a list of personal access tokens.

Modifications:
- Exclude HCL and VBA from Programming languages output.
- Add Data Science & AI category and detect common DS/AI libraries by:
  - scanning dependency files (requirements.txt, pyproject.toml, environment.yml, package.json)
  - scanning source files (.py, .ipynb) for import/from statements
  - scanning content blobs for known DS keywords
Other behavior unchanged.
"""
import os
import requests
import time
import urllib.parse
import re
import json
from collections import Counter

GITHUB_API = "https://api.github.com"
OWNER = os.getenv("OWNER")
REPO = os.getenv("REPO")

# Multiple token support:
ACCESS_TOKENS = os.getenv("ACCESS_TOKENS")  # comma-separated
ACCESS_TOKEN = os.getenv("ACCESS_TOKEN")    # legacy single token

TOKENS = []
if ACCESS_TOKENS:
    for t in ACCESS_TOKENS.split(","):
        t = t.strip()
        if t:
            TOKENS.append(t)
elif ACCESS_TOKEN:
    TOKENS.append(ACCESS_TOKEN)

REQUEST_TIMEOUT = 30
repo_token_map = {}  # repo_str -> token that discovered it

# --- detection heuristics maps ---
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
}

PACKAGE_MANAGERS = {
    "npm", "yarn", "pip", "pipenv", "poetry", "composer", "cargo", "bundler", "gem"
}

# skills to exclude from final badges
FORBIDDEN_SKILLS = {
    "Jupyter Notebook", "Dockerfile", "CSS", "HTML", "Shell"
}

# Programming languages normalization (GitHub languages)
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
    "HCL": "HCL",
    "VBA": "VBA",
}

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
}

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

SERVICE_KEYWORDS = {
    "django": "Django",
    "flask": "Flask",
    "fastapi": "FastAPI",
    "express": "Express",
    "spring": "Spring",
    "laravel": "Laravel",
    "asp.net": "ASP.NET",
    "graphql": "GraphQL",
    "rabbitmq": "RabbitMQ",
    "kafka": "Kafka",
}

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
    "aws": "AWS",
    "amazon web services": "AWS",
    "gcp": "GCP",
    "google cloud": "GCP",
    "azure": "Azure",
    "microsoft azure": "Azure",
    "prometheus": "Prometheus",
    "grafana": "Grafana",
    "nginx": "Nginx",
    "chef": "Chef",
    "consul": "Consul",
}

# Data Science & AI keywords mapping (package name -> normalized label)
DS_LIB_KEYWORDS = {
    "numpy": "NumPy",
    "np": "NumPy",
    "pandas": "Pandas",
    "pd": "Pandas",
    "scikit-learn": "Scikit-learn",
    "sklearn": "Scikit-learn",
    "torch": "PyTorch",
    "pytorch": "PyTorch",
    "tensorflow": "TensorFlow",
    "tf": "TensorFlow",
    "hydra": "Hydra",
    "hydra-core": "Hydra",
    "wandb": "WandB",
    "optuna": "Optuna",
    "transformers": "HuggingFace",
    "datasets": "HuggingFace",
    "sentence-transformers": "HuggingFace",
    "keras": "Keras",
    "xgboost": "XGBoost",
    "lightgbm": "LightGBM",
    "catboost": "CatBoost",
    "spacy": "spaCy",
    "statsmodels": "Statsmodels",
    "scipy": "SciPy",
    "gensim": "Gensim",
    "fastai": "fastai",
    # add others if needed
}

category_map = {
    "Programming languages": Counter(),
    "Frontend development": Counter(),
    "Data Science & AI": Counter(),          # NEW category
    "Misc tools": Counter(),
    "Services & Frameworks": Counter(),
    "Databases": Counter(),
    "DevOps": Counter(),
}

# --- helpers for API calls with optional token override ---
def mk_headers(token=None):
    headers = {"Accept": "application/vnd.github.v3+json"}
    if token:
        headers["Authorization"] = f"token {token}"
    return headers

def api_get(path, params=None, token=None):
    url = f"{GITHUB_API}{path}"
    headers = mk_headers(token=token)
    r = requests.get(url, headers=headers, params=params, timeout=REQUEST_TIMEOUT)
    r.raise_for_status()
    return r.json()

# --- list repos discovered by each token ---
def list_repos_for_token(token):
    repos = []
    per_page = 100
    page = 1
    while True:
        try:
            data = api_get("/user/repos", params={"per_page": per_page, "page": page, "visibility": "all", "affiliation": "owner,collaborator,organization_member"}, token=token)
        except requests.HTTPError as e:
            print(f"Warning: failed to list /user/repos for a token: {e}")
            break
        if not data:
            break
        repos.extend(data)
        if len(data) < per_page:
            break
        page += 1
        time.sleep(0.05)
    return repos

def list_public_user_repos(owner):
    repos = []
    per_page = 100
    page = 1
    while True:
        try:
            data = api_get(f"/users/{owner}/repos", params={"per_page": per_page, "page": page, "type": "all"})
        except requests.HTTPError:
            break
        if not data:
            break
        repos.extend(data)
        if len(data) < per_page:
            break
        page += 1
    return repos

def list_all_repos():
    repo_set = set()
    if TOKENS:
        for token in TOKENS:
            api_items = list_repos_for_token(token)
            for r in api_items:
                try:
                    if r.get("fork"):
                        continue
                    repo_str = f"{r['owner']['login']}/{r['name']}"
                    if repo_str not in repo_set:
                        repo_set.add(repo_str)
                        repo_token_map[repo_str] = token
                except Exception:
                    continue
    else:
        if OWNER:
            pubs = list_public_user_repos(OWNER)
            for r in pubs:
                try:
                    if r.get("fork"):
                        continue
                    repo_str = f"{r['owner']['login']}/{r['name']}"
                    if repo_str not in repo_set:
                        repo_set.add(repo_str)
                        repo_token_map[repo_str] = None
                except Exception:
                    continue
    return sorted(repo_set)

# --- repository file access helpers ---
def get_repo_default_branch(owner, repo):
    repo_str = f"{owner}/{repo}"
    token = repo_token_map.get(repo_str)
    try:
        data = api_get(f"/repos/{owner}/{repo}", token=token)
        return data.get("default_branch", "main")
    except Exception:
        return "main"

def get_tree(owner, repo, branch):
    repo_str = f"{owner}/{repo}"
    token = repo_token_map.get(repo_str)
    try:
        return api_get(f"/repos/{owner}/{repo}/git/trees/{branch}", params={"recursive": "1"}, token=token)
    except Exception:
        return {"tree": []}

def get_file_content(owner, repo, path):
    repo_str = f"{owner}/{repo}"
    token = repo_token_map.get(repo_str)
    try:
        r = requests.get(f"{GITHUB_API}/repos/{owner}/{repo}/contents/{urllib.parse.quote(path, safe='')}",
                         headers=mk_headers(token=token),
                         timeout=REQUEST_TIMEOUT)
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

def scan_file_text_for_keywords(text, keywords_map):
    found = set()
    txt = (text or "").lower()
    for k, label in keywords_map.items():
        if k.lower() in txt:
            found.add(label)
    return found

# find imports in python source text
_import_re = re.compile(r'^\s*(?:from\s+([A-Za-z0-9_\.]+)\s+import|import\s+([A-Za-z0-9_\.]+))', re.MULTILINE)

def extract_imports_from_py(text):
    found = set()
    for m in _import_re.finditer(text):
        mod = m.group(1) or m.group(2)
        if not mod:
            continue
        base = mod.split('.')[0].lower()
        found.add(base)
    return found

def extract_imports_from_ipynb(nb_text):
    found = set()
    try:
        nb = json.loads(nb_text)
        for cell in nb.get("cells", []):
            if cell.get("cell_type") != "code":
                continue
            src = "".join(cell.get("source", []) if isinstance(cell.get("source", []), list) else cell.get("source", ""))
            found |= extract_imports_from_py(src)
    except Exception:
        pass
    return found

# --- detection per repo ---
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
        "datasci": set(),   # new
        "misc": set(),
    }
    # languages via API
    try:
        repo_str = f"/repos/{owner}/{r}/languages"
        token = repo_token_map.get(f"{owner}/{r}")
        langs = api_get(repo_str, token=token)
        for L in langs.keys():
            if L in LANGUAGE_NORMALIZE:
                detected["languages"][LANGUAGE_NORMALIZE[L]] = langs[L]
            else:
                detected["languages"][L] = langs[L]
    except Exception:
        pass

    candidates_to_fetch = []
    for p in paths:
        basename = os.path.basename(p).lower()
        if basename in FILE_TOOL_MAP:
            candidates_to_fetch.append(p)
        if p.endswith(".tf"):
            candidates_to_fetch.append(p)
        if p.endswith(".sql"):
            candidates_to_fetch.append(p)
        if "dockerfile" in basename:
            candidates_to_fetch.append(p)
        if basename in {".env", ".env.example", ".env.sample", "database.yml", "database.yaml", "application.yml", "config.yml", "environment.yml"}:
            candidates_to_fetch.append(p)
        if basename in {"package.json", "requirements.txt", "pyproject.toml", "pom.xml", "go.mod", "composer.json", "Gemfile", "Cargo.toml", "environment.yml"}:
            candidates_to_fetch.append(p)

        # cloud/ops filenames
        if any(key in basename for key in ("cloudformation", "serverless.yml", "serverless.yaml",
                                           "azure-pipelines.yml", "cloudbuild.yaml",
                                           "sam.yaml", "prometheus.yml", "grafana.ini",
                                           "nginx.conf", "consul.hcl", "berksfile")):
            candidates_to_fetch.append(p)
        if "/recipes/" in p.lower() or basename == "metadata.rb" or basename == "berksfile":
            candidates_to_fetch.append(p)
        if p.lower().endswith("chart.yaml") or "/charts/" in p.lower():
            candidates_to_fetch.append(p)
        # include python and notebook files for import scanning
        if p.lower().endswith(".py") or p.lower().endswith(".ipynb"):
            candidates_to_fetch.append(p)

    content_blob = ""
    code_imports = set()
    # fetch candidate files (content_blob accumulates text for keyword scanning)
    for path in sorted(set(candidates_to_fetch)):
        txt = get_file_content(owner, r, path)
        if not txt:
            continue
        lpath = path.lower()
        # accumulate for keyword scans
        content_blob += "\n" + txt.lower()
        # if python file, extract imports
        if lpath.endswith(".py"):
            code_imports |= extract_imports_from_py(txt)
        elif lpath.endswith(".ipynb"):
            code_imports |= extract_imports_from_ipynb(txt)
        time.sleep(0.06)

    # DB detection
    dbs_found = scan_file_text_for_keywords(content_blob, DB_KEYWORDS)
    for d in dbs_found:
        detected["dbs"].add(d)

    # Frontend detection
    fe_found = scan_file_text_for_keywords(content_blob, FRONTEND_KEYWORDS)
    for f in fe_found:
        detected["frontend"].add(f)

    # Service/framework detection
    svc_found = scan_file_text_for_keywords(content_blob, SERVICE_KEYWORDS)
    for s in svc_found:
        detected["services"].add(s)

    # DevOps detection via DEVOPS_KEYWORDS
    dev_found = scan_file_text_for_keywords(content_blob, DEVOPS_KEYWORDS)
    for d in dev_found:
        detected["devops"].add(d)

    # Additional cloud/ops pattern detection
    CLOUD_PATTERNS = {
        'provider "aws"': "AWS",
        'provider "google"': "GCP",
        'provider "google-beta"': "GCP",
        'provider "azurerm"': "Azure",
        "cloudformation": "AWS",
        "serverless": "AWS",
        "sam": "AWS",
        "gcloud": "GCP",
        "google cloud": "GCP",
        "azurerm": "Azure",
        "azure-pipelines": "Azure",
    }
    OPS_PATTERNS = {
        "prometheus": "Prometheus",
        "grafana": "Grafana",
        "nginx": "Nginx",
        "chef": "Chef",
        "consul": "Consul",
        "prometheus.yml": "Prometheus",
        "grafana.ini": "Grafana",
        "consul.hcl": "Consul",
        "berksfile": "Chef",
        "recipes/": "Chef",
    }

    for k, name in CLOUD_PATTERNS.items():
        if k in content_blob:
            detected["devops"].add(name)

    for k, name in OPS_PATTERNS.items():
        if k in content_blob:
            detected["devops"].add(name)

    # heuristics
    if "kubernetes" in content_blob or "k8s" in content_blob:
        detected["devops"].add("Kubernetes")
    if ".github/workflows" in "\n".join(paths) or "github actions" in content_blob:
        detected["devops"].add("GitHub Actions")
    if "terraform" in content_blob:
        detected["devops"].add("Terraform")
    if "helm" in content_blob:
        detected["devops"].add("Helm")
    if "prometheus" in content_blob:
        detected["devops"].add("Prometheus")
    if "grafana" in content_blob:
        detected["devops"].add("Grafana")
    if "nginx" in content_blob:
        detected["devops"].add("Nginx")
    if "consul" in content_blob:
        detected["devops"].add("Consul")
    if "chef" in content_blob or "cookbook" in content_blob:
        detected["devops"].add("Chef")

    # --- Data Science & AI detection ---
    # 1) keyword scan in content_blob
    ds_found = scan_file_text_for_keywords(content_blob, DS_LIB_KEYWORDS)
    for d in ds_found:
        detected["datasci"].add(d)

    # 2) code import scan (stronger signal)
    for imp in code_imports:
        if imp in DS_LIB_KEYWORDS:
            detected["datasci"].add(DS_LIB_KEYWORDS[imp])
        # also check for common aliases e.g., "np" -> numpy handled in mapping

    # 3) requirements/environment files parsing: look for package tokens
    # (note: content_blob already includes requirements files' content if present)
    # we've already matched via ds_found above, so this is complementary.

    return detected

# --- badge generation (unchanged format) ---
def badge_url_for(skill_name):
    label = urllib.parse.quote(f"-{skill_name}")
    logo = urllib.parse.quote(skill_name)
    return f"https://img.shields.io/badge/{label}-000?&logo={logo}"

def build_badge_md(skill_name):
    url = badge_url_for(skill_name)
    return f"![{skill_name}]({url})"

# --- main aggregation & README write ---
repos = list_all_repos()
if not repos:
    print("No repos found; exiting.")
    exit(0)

aggregate = {
    "languages": Counter(),
    "frontend": Counter(),
    "datasci": Counter(),     # new
    "services": Counter(),
    "dbs": Counter(),
    "devops": Counter(),
    "misc": Counter(),
}

for full in repos:
    try:
        det = detect_for_repo(full)
    except Exception as e:
        print(f"Error detecting {full}: {e}")
        continue
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
    for ds in det.get("datasci", []):
        aggregate["datasci"][ds] += 1

def add_skill(category, name):
    # exclude package managers and forbidden skills
    if name in PACKAGE_MANAGERS:
        return
    if name in FORBIDDEN_SKILLS:
        return
    # exclude HCL and VBA from Programming languages output
    if category == "Programming languages" and name in ("HCL", "VBA"):
        return
    category_map[category][name] += 1

for lang, _ in aggregate["languages"].most_common():
    add_skill("Programming languages", lang)

for f, _ in aggregate["frontend"].most_common():
    add_skill("Frontend development", f)

for ds, _ in aggregate["datasci"].most_common():
    add_skill("Data Science & AI", ds)

for s, _ in aggregate["services"].most_common():
    add_skill("Services & Frameworks", s)

for d, _ in aggregate["dbs"].most_common():
    add_skill("Databases", d)

for dv, _ in aggregate["devops"].most_common():
    add_skill("DevOps", dv)

# Build sections in order (include the new category)
sections = []
order_of_categories = [
    "Programming languages",
    "Frontend development",
    "Data Science & AI",    # new
    "Misc tools",
    "Services & Frameworks",
    "Databases",
    "DevOps"
]

for cat in order_of_categories:
    counter = category_map.get(cat, Counter())
    if not counter:
        continue
    items = [k for k, _ in counter.most_common()]
    badges = []
    for it in items:
        md = build_badge_md(it)
        if md:
            badges.append(md)
    if badges:
        sections.append(f"### {cat}\n\n{' '.join(badges)}\n")

if not sections:
    new_section = "## 🛠️ My Skills\n\n_No detected skills._\n"
else:
    new_section = "## 🛠️ My Skills\n\n" + "\n\n".join(sections)

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

# commit & push (safe: skip if no changes)
import subprocess, sys

try:
    subprocess.run(["git", "config", "user.name", "github-actions"], check=True)
    subprocess.run(["git", "config", "user.email", "github-actions@users.noreply.github.com"], check=True)

    subprocess.run(["git", "add", README_PATH], check=True)
    status = subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True, check=True)
    if not status.stdout.strip():
        print("No changes to commit. Skipping commit and push.")
        sys.exit(0)

    subprocess.run(["git", "commit", "-m", "chore: update categorized My Skills badges [skip ci]"], check=True)

    push_token = (TOKENS[0] if TOKENS else None) or os.getenv("GITHUB_TOKEN")
    if not push_token:
        print("No token available to push changes. Please set ACCESS_TOKENS or ACCESS_TOKEN or GITHUB_TOKEN.")
        sys.exit(0)

    branch = os.getenv("GITHUB_REF_NAME") or "main"
    repo_url = f"https://{push_token}@github.com/{OWNER}/{REPO}.git"

    subprocess.run(["git", "push", repo_url, f"HEAD:refs/heads/{branch}"], check=True)
    print("README updated and pushed successfully.")
except subprocess.CalledProcessError as e:
    print("Git error (commit/push):", e)
    try:
        subprocess.run(["git", "status"], check=False)
        subprocess.run(["git", "log", "-1", "--oneline"], check=False)
    except Exception:
        pass
    sys.exit(1)
