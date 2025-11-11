#!/usr/bin/env python3
# .github/scripts/update_my_skills.py
import os
import requests
import base64
import json
import urllib.parse
from collections import Counter, defaultdict

GITHUB_API = "https://api.github.com"
OWNER = os.getenv("OWNER")
REPO = os.getenv("REPO")
ACCESS_TOKEN = os.getenv("ACCESS_TOKEN")  # optional: PAT for broader access
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
REPO_LIST = os.getenv("REPO_LIST")  # optional list "owner/repo,owner2/repo2"

HEADERS = {}
if ACCESS_TOKEN:
    HEADERS["Authorization"] = f"token {ACCESS_TOKEN}"
elif GITHUB_TOKEN:
    HEADERS["Authorization"] = f"token {GITHUB_TOKEN}"

# --- Helpers ---
def api_get(path, params=None):
    url = f"{GITHUB_API}{path}"
    r = requests.get(url, headers=HEADERS, params=params, timeout=30)
    r.raise_for_status()
    return r.json()

def list_all_repos():
    """
    If REPO_LIST provided, use that. Otherwise, list repos for the authenticated user/organization.
    By default will list owner repos if OWNER provided.
    """
    if REPO_LIST:
        return [r.strip() for r in REPO_LIST.split(",") if r.strip()]

    repos = []
    page = 1
    while True:
        # list repos for the owner (user or org)
        path = f"/users/{OWNER}/repos"
        params = {"per_page": 100, "page": page, "type": "all"}
        data = api_get(path, params=params)
        if not data:
            break
        repos.extend([f"{r['owner']['login']}/{r['name']}" for r in data])
        if len(data) < 100:
            break
        page += 1
    return repos

# language aggregation
lang_bytes = Counter()
tool_counter = Counter()
repo_details = {}

# detection rules: filename patterns -> tool name(s)
FILE_TOOL_MAP = {
    "package.json": ["npm", "nodejs"],
    "pyproject.toml": ["python", "poetry"],
    "requirements.txt": ["python", "pip"],
    "Pipfile": ["python", "pipenv"],
    "poetry.lock": ["python", "poetry"],
    "go.mod": ["go"],
    "pom.xml": ["maven"],
    "build.gradle": ["gradle"],
    "Gemfile": ["ruby"],
    "composer.json": ["php"],
    "Dockerfile": ["docker"],
    "webpack.config.js": ["webpack"],
    "vite.config.js": ["vite"],
    "next.config.js": ["next.js"],
    "nuxt.config": ["nuxt"],
    "angular.json": ["angular"],
    "Cargo.toml": ["rust"],
    "requirements-dev.txt": ["python"],
    "yarn.lock": ["yarn"],
    "package-lock.json": ["npm"],
    "composer.lock": ["composer"],
    "terraform": ["terraform"],  # we'll look for .tf files
    "Dockerfile.dev": ["docker"]
}

# mapping for shields logos
LOGO_MAP = {
    "python":"python","nodejs":"node.js","npm":"npm","pip":"pip","go":"go","java":"java",
    "docker":"docker","rust":"rust","php":"php","ruby":"ruby","maven":"maven",
    "gradle":"gradle","next.js":"next.js","vite":"vite","webpack":"webpack",
    "yarn":"yarn","terraform":"terraform","postgresql":"postgresql","mysql":"mysql",
}

def detect_tools_from_tree(owner, repo):
    """
    Use the git/trees recursive API to list files and detect known files.
    """
    try:
        # get default branch
        repo_meta = api_get(f"/repos/{owner}/{repo}")
        default_branch = repo_meta.get("default_branch", "main")
        tree = api_get(f"/repos/{owner}/{repo}/git/trees/{default_branch}", params={"recursive": "1"})
        files = [e["path"] for e in tree.get("tree", []) if e["type"] == "blob"]
    except Exception:
        return []

    detected = set()
    for f in files:
        fname = os.path.basename(f)
        if fname in FILE_TOOL_MAP:
            for t in FILE_TOOL_MAP[fname]:
                detected.add(t)
        # extension-based
        if f.endswith(".tf"):
            detected.add("terraform")
        if f.endswith(".sql"):
            # DB usage is fuzzy: presence of .sql doesn't mean specific DB
            detected.add("sql")
        if f.lower().startswith("dockerfile"):
            detected.add("docker")
        # quick heuristics for DB config (common filenames)
        if f.lower().endswith("database.yml") or f.lower().endswith("database.yaml"):
            detected.add("database-yaml")
    return list(detected)

def get_repo_languages(owner, repo):
    try:
        data = api_get(f"/repos/{owner}/{repo}/languages")
        return data  # dict of language -> bytes
    except Exception:
        return {}

# main
repos = list_all_repos()
if not repos:
    print("No repos found")
else:
    print("Analyzing repos:", repos)

for full in repos:
    try:
        owner, r = full.split("/", 1)
    except ValueError:
        continue
    langs = get_repo_languages(owner, r)
    for L, B in langs.items():
        lang_bytes[L] += B
    tools = detect_tools_from_tree(owner, r)
    for t in tools:
        tool_counter[t] += 1
    repo_details[full] = {"languages": langs, "tools": tools}

# aggregate results
def top_n(counter, n=8):
    return [k for k,_ in counter.most_common(n)]

top_languages = top_n(lang_bytes, 8)
top_tools = top_n(tool_counter, 12)

print("Top languages:", top_languages)
print("Top tools:", top_tools)

# build badges via shields.io
def badge_url(label, message, logo=None, color="blue", style="for-the-badge"):
    label_enc = urllib.parse.quote(label)
    message_enc = urllib.parse.quote(message)
    url = f"https://img.shields.io/badge/{label_enc}-{message_enc}-{color}?style={style}"
    if logo:
        url += f"&logo={urllib.parse.quote(logo)}"
    return url

# produce markdown for My Skills
lines = []
lines.append("## My Skills")
lines.append("")
# Languages row
lang_badges = []
for lang in top_languages:
    # show short percent or bytes
    total = sum(lang_bytes.values()) or 1
    pct = (lang_bytes[lang] / total) * 100
    msg = f"{lang} {pct:.0f}%"
    logo = LOGO_MAP.get(lang.lower(), None)
    url = badge_url(lang, msg, logo=logo if logo else None)
    lang_badges.append(f"![{lang}]({url})")
if lang_badges:
    lines.append(" ".join(lang_badges))
    lines.append("")

# Tools row
tool_badges = []
for t in top_tools:
    msg = f"{tool_counter[t]} repos"
    logo = LOGO_MAP.get(t, None)
    url = badge_url(t, msg, logo=logo if logo else None)
    tool_badges.append(f"![{t}]({url})")
if tool_badges:
    lines.append(" ".join(tool_badges))
    lines.append("")

# Put other metadata if desired
lines.append("> _Generated by GitHub Actions — last updated automatically._")
new_section = "\n".join(lines)

# Read README and replace between markers
README_PATH = "README.md"
START = "<!-- SKILLS-START -->"
END = "<!-- SKILLS-END -->"

if not os.path.exists(README_PATH):
    print("README.md not found in repo root. Creating a new one.")
    with open(README_PATH, "w", encoding="utf-8") as f:
        f.write(f"{START}\n{new_section}\n{END}\n")
else:
    with open(README_PATH, "r", encoding="utf-8") as f:
        text = f.read()
    if START in text and END in text:
        before, rest = text.split(START, 1)
        _, after = rest.split(END, 1)
        new_text = before + START + "\n" + new_section + "\n" + END + after
    else:
        # append markers and section at the end
        new_text = text + "\n\n" + START + "\n" + new_section + "\n" + END + "\n"
    with open(README_PATH, "w", encoding="utf-8") as f:
        f.write(new_text)

# Commit changes (using git)
import subprocess, sys
try:
    subprocess.run(["git", "config", "user.name", "github-actions"], check=True)
    subprocess.run(["git", "config", "user.email", "github-actions@users.noreply.github.com"], check=True)
    subprocess.run(["git", "add", README_PATH], check=True)
    subprocess.run(["git", "commit", "-m", "chore: update My Skills badges [skip ci]"], check=True)
    # push using token (if ACCESS_TOKEN set, use it; otherwise GITHUB_TOKEN)
    push_token = ACCESS_TOKEN or GITHUB_TOKEN
    if not push_token:
        print("No token available to push changes. Please set ACCESS_TOKEN or rely on GITHUB_TOKEN.")
        sys.exit(0)
    repo_url = f"https://{push_token}@github.com/{OWNER}/{REPO}.git"
    subprocess.run(["git", "push", repo_url, "HEAD:refs/heads/"+os.getenv("GITHUB_REF_NAME", "main")], check=True)
except subprocess.CalledProcessError as e:
    print("Git error:", e)
