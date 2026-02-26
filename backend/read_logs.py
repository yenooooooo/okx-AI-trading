import os

log_path = r"c:\Users\ailee\github\okx\backend\logs\backend.log"
if os.path.exists(log_path):
    with open(log_path, 'r', encoding='utf-8', errors='replace') as f:
        lines = f.readlines()
        for line in lines[-50:]:
            print(line, end='')
else:
    print(f"Log not found at {log_path}")
