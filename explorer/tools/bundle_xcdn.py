"""Bundle the official xCDN JavaScript library (ES modules) into one classic script.

The Explorer must also work when index.html is opened straight from disk, where browsers
refuse ES module imports. Usage:

    python explorer/tools/bundle_xcdn.py PATH/TO/xCDN-JavaScript explorer/vendor/xcdn.js
"""

import json
import re
import sys
from pathlib import Path

ORDER = ("error.js", "ast.js", "lexer.js", "parser.js", "serializer.js")
EXPORTS = ("parseStr", "toStringPretty", "toStringCompact", "Document", "Node", "XObject", "XArray",
           "XString", "Int", "Float", "Bool", "Null", "DecimalValue", "Bytes", "DateTime", "Duration", "Uuid",
           "Tag", "Annotation", "XCDNError")


def bundle(source, target):
    source = Path(source)
    package = json.loads((source / "package.json").read_text(encoding="utf8"))
    parts = []
    for name in ORDER:
        text = (source / "src" / name).read_text(encoding="utf8")
        text = re.sub(r"^import\s*\{[^}]*\}\s*from\s*'[^']+';\s*$", "", text, flags=re.M | re.S)
        text = re.sub(r"^import[^\n]*\n", "", text, flags=re.M)
        text = re.sub(r"^export\s*\{[^}]*\};?\s*$", "", text, flags=re.M)
        text = re.sub(r"^export\s+(?=(class|function|const|let|async)\b)", "", text, flags=re.M)
        parts.append(f"// ---- {name}\n{text.strip()}\n")
    licence = (source / "LICENSE").read_text(encoding="utf8").strip()
    header = (f"/* xCDN JavaScript {package['version']} (https://github.com/gslf/xCDN-JavaScript), bundled as a classic\n"
              "   script for the UNIMATRIx Explorer by explorer/tools/bundle_xcdn.py.\n\n"
              + "\n".join("   " + line if line else "" for line in licence.splitlines()) + "\n*/\n")
    body = "\n".join(parts)
    exported = ", ".join(EXPORTS)
    target = Path(target)
    target.write_text(f'{header}(function (root) {{\n"use strict";\n{body}\n'
                      f"root.xcdn = Object.freeze({{ version: {json.dumps(package['version'])}, {exported},\n"
                      "  parse: parseStr, stringify: toStringPretty, stringifyCompact: toStringCompact });\n"
                      "})(typeof window !== \"undefined\" ? window : globalThis);\n", encoding="utf8")
    return target


if __name__ == "__main__":
    print(bundle(sys.argv[1], sys.argv[2]))
