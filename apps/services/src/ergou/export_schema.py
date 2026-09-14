import argparse
import json
from pathlib import Path

from .app import create_app
from .schemas import TaskEvent
from .workspace import repository_root


def main():
    parser = argparse.ArgumentParser(description="导出 Ergou OpenAPI 契约")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    root = repository_root()
    schema = create_app().openapi()
    event_schema = TaskEvent.model_json_schema(ref_template="#/components/schemas/{model}")
    definitions = event_schema.pop("$defs", {})
    schema["components"]["schemas"].update(definitions)
    schema["components"]["schemas"]["TaskEvent"] = event_schema
    output = args.output or root / "packages" / "contracts" / "openapi.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(schema, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
