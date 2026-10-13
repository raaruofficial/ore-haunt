#!/usr/bin/env python3
"""Chunk-rail ingest decoder (ORE HAUNT).

Large/binary files that cannot travel through the GitHub connector arrive as
base64 chunks under ingest/<key>.b64.NN. This script reassembles each payload,
verifies size + md5 against the embedded manifest, writes it to its target
path(s), and removes the ingest directory. Any mismatch aborts with exit 1
before anything is committed. Run by .github/workflows/ingest.yml on the
ingest-drop branch; the decoded tree is merged to main via PR.
"""
import base64, glob, hashlib, os, shutil, sys

MANIFEST = {
 "haunt": {
  "size": 584802,
  "md5": "9b2bfd4d1bf082e9716b9dc242b2f44f",
  "chunks": 18
 },
 "buildapp": {
  "size": 188476,
  "md5": "8e4feb64a944cc045b165a194d545d73",
  "chunks": 6
 },
 "deck": {
  "size": 548315,
  "md5": "10280c7a713fb239f56e1a34abc1b406",
  "chunks": 17
 },
 "shot-leaderboard": {
  "size": 108508,
  "md5": "a448f1e0e7783414750537e916a350d4",
  "chunks": 4
 },
 "shot-dossier": {
  "size": 109571,
  "md5": "346641e99eef0285ddb7268eaa5277c0",
  "chunks": 4
 },
 "shot-player": {
  "size": 102950,
  "md5": "d2eaf480cc42ec7e4b82ac1bf5d55798",
  "chunks": 4
 },
 "shot-replay": {
  "size": 153782,
  "md5": "e079cff5f1a39f6dbb2530e3181aea62",
  "chunks": 5
 }
}
TARGETS = {
 "haunt": [
  "app/haunt.html",
  "app/src/main/assets/game/index.html"
 ],
 "buildapp": [
  "app/build_app.py"
 ],
 "deck": [
  "ORE_HAUNT_CLOCKIN_DECK.pdf"
 ],
 "shot-leaderboard": [
  "screenshots/leaderboard.png"
 ],
 "shot-dossier": [
  "screenshots/dossier.png"
 ],
 "shot-player": [
  "screenshots/player.png"
 ],
 "shot-replay": [
  "screenshots/replay.png"
 ]
}

def main():
    if not os.path.isdir('ingest'):
        print('no ingest/ directory - nothing to do')
        return 0
    keys = [k for k in MANIFEST if glob.glob(f'ingest/{k}.b64.*')]
    if not keys:
        print('no chunk files - nothing to do')
        return 0
    for key in keys:
        parts = sorted(glob.glob(f'ingest/{key}.b64.*'))
        if len(parts) != MANIFEST[key]['chunks']:
            print(f'{key}: expected {MANIFEST[key]["chunks"]} chunks, found {len(parts)}')
            return 1
        blob = base64.b64decode(''.join(open(p).read() for p in parts), validate=True)
        if len(blob) != MANIFEST[key]['size'] or hashlib.md5(blob).hexdigest() != MANIFEST[key]['md5']:
            print(f'{key}: size/md5 mismatch after decode - aborting')
            return 1
        for target in TARGETS[key]:
            os.makedirs(os.path.dirname(target) or '.', exist_ok=True)
            with open(target, 'wb') as f:
                f.write(blob)
        print(f'{key}: OK -> {TARGETS[key]}')
    shutil.rmtree('ingest')
    print('ingest complete, chunks removed')
    return 0

if __name__ == '__main__':
    sys.exit(main())
