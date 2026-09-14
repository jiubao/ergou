import json
from pathlib import Path
from .app import create_app
from .schemas import TaskEvent


def main():
    root = Path(__file__).resolve().parents[3]
    schema = create_app().openapi()
    event_schema = TaskEvent.model_json_schema(ref_template="#/components/schemas/{model}")
    definitions = event_schema.pop("$defs", {})
    schema["components"]["schemas"].update(definitions)
    schema["components"]["schemas"]["TaskEvent"] = event_schema
    (root / "contracts" / "openapi.json").write_text(json.dumps(schema, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
