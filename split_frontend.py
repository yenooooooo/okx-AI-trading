import os
import sys
import io

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

SOURCE = r'C:\Users\ailee\github\okx\frontend\api_client.js'
OUT_DIR = r'C:\Users\ailee\github\okx\frontend\modules'

# (module_name, [(start_line, end_line), ...])  — 1-indexed, inclusive
modules = [
    ('state',           [(1, 28)]),
    ('ui',              [(29, 49), (177, 320), (2376, 2393), (4931, 4956), (4975, 5044)]),
    ('sync',            [(50, 176), (321, 725), (726, 935), (1346, 1358), (1581, 1722), (3852, 3888)]),
    ('gates',           [(936, 1345)]),
    ('presets',         [(1359, 1580), (1723, 1774)]),
    ('tuning',          [(1775, 1854), (2284, 2375), (5045, 5080)]),
    ('diagnostic',      [(1855, 2265)]),
    ('manual_override', [(2266, 2283), (3539, 3695), (4957, 4974)]),
    ('scanner',         [(2394, 2475), (3889, 3988)]),
    ('chart',           [(2476, 2796)]),
    ('terminal',        [(2797, 3031)]),
    ('analytics',       [(3032, 3268), (4057, 4459)]),
    ('backtest',        [(3269, 3538)]),
    ('stress',          [(3696, 3751), (4460, 4585)]),
    ('websocket',       [(3752, 3851)]),
    ('main',            [(3989, 4056)]),
    ('xray',            [(4586, 4930)]),
]

with open(SOURCE, 'r', encoding='utf-8') as f:
    lines = f.readlines()

total = len(lines)
print(f"Source file: {total} lines")

os.makedirs(OUT_DIR, exist_ok=True)

covered = set()
for name, ranges in modules:
    parts = []
    for start, end in ranges:
        chunk = lines[start - 1:end]
        parts.append(''.join(chunk))
        for i in range(start, end + 1):
            covered.add(i)
    content = '\n'.join(parts)
    out_path = os.path.join(OUT_DIR, f'{name}.js')
    with open(out_path, 'w', encoding='utf-8') as f:
        f.write(content)
    print(f"  {name}.js — ranges {ranges} => {sum(e-s+1 for s,e in ranges)} lines written")

uncovered = sorted(set(range(1, total + 1)) - covered)
print(f"\nTotal lines: {total} / Covered: {len(covered)} / Uncovered: {len(uncovered)}")
if uncovered:
    for ln in uncovered:
        print(f"  {ln}: {repr(lines[ln-1])}")
else:
    print("All lines covered!")
