#!/usr/bin/env bash
set -euo pipefail
ART_DIR=${1:-artifacts}
OUT_HTML=${ART_DIR}/e2e_report.html
mkdir -p ${ART_DIR}

cat > ${OUT_HTML} <<'HTML'
<!doctype html>
<html>
<head><meta charset="utf-8"><title>E2E Report</title></head>
<body>
<h1>E2E Run Report</h1>
<p>Artifacts collected during E2E run:</p>
<ul>
HTML

for f in $(find artifacts -type f | sort); do
  rel=${f#artifacts/}
  echo "<li><a href='./${rel}'>${rel}</a></li>" >> ${OUT_HTML}
done

cat >> ${OUT_HTML} <<'HTML'
</ul>
</body>
</html>
HTML

echo "Generated report: ${OUT_HTML}"
