"""lab_registry/ 폴더의 PC 등록 파일 → lab_pcs.json 생성."""
import json
from pathlib import Path

registry = Path(__file__).resolve().parent.parent / "artifacts" / "lab_registry"
output = Path(__file__).resolve().parent / "lab_pcs.json"

pcs = []
for f in sorted(registry.glob("*.txt")):
    line = f.read_text().strip()
    parts = line.split(",")
    if len(parts) >= 3:
        pcs.append({"id": len(pcs) + 1, "hostname": parts[0], "ip": parts[1], "mac": parts[2]})

output.write_text(json.dumps(pcs, indent=2, ensure_ascii=False))
print(f"{len(pcs)}대 등록 → {output}")
for pc in pcs:
    print(f"  PC{pc['id']}: {pc['ip']}  {pc['mac']}  {pc['hostname']}")
