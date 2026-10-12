# ORE HAUNT — CLOCK IN submission repo: push steps

This folder IS the repo root: the Android project (CI builds the APK) plus
app/ (web build source), backend/, and data-pipeline/ as evidence of the
full stack.

1. On github.com (signed in as raaruofficial): New repository ->
   name: ore-haunt -> Create. Do NOT add README/gitignore/license
   (this folder already has them).
2. Then, from a terminal:

cd ~/workspace/your_files/ore-haunt/clock-in/submission-repo
git init -b main
git add -A
git commit -m "ORE HAUNT — CLOCK IN demo milestone"
git remote add origin https://github.com/raaruofficial/ore-haunt.git
git push -u origin main

3. In the new repo: Settings -> Secrets and variables -> Actions ->
   add the same 4 secrets as the other entries:
   KEYSTORE_BASE64, KEYSTORE_PASSWORD, KEY_ALIAS, KEY_PASSWORD.
   The push triggers CI; the signed release APK builds on tag v0.1.0,
   and every push also produces the orehaunt-debug-apk artifact.
