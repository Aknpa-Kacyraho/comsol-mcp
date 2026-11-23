"""校验 DSH 的 cordis.patch.yml（含 !!js 自定义标签）。"""
import sys

import yaml


class JsLoader(yaml.SafeLoader):
    pass


JsLoader.add_constructor(
    "tag:yaml.org,2002:js", lambda loader, node: f"<js:{node.value}>"
)
JsLoader.add_multi_constructor(
    "!", lambda loader, suffix, node: f"<js:{node.value}>"
)

path = sys.argv[1]
with open(path, encoding="utf-8") as fh:
    data = yaml.load(fh, Loader=JsLoader)

insert = data[0]["insert"]
print(f"entries: {len(insert)}")
for item in insert:
    cfg = item["config"]
    print(f" - {item['id']}: {cfg['serverName']} <- {cfg['command']}")
