"""Evaluation runner: tests routing accuracy against dataset.json."""
import asyncio
import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)
DATASET_PATH = Path(__file__).parent / "dataset.json"


async def run_eval_suite(dataset_path: str | None = None) -> dict:
    from agents.router_agent import classify_intent

    path = dataset_path or str(DATASET_PATH)
    with open(path, encoding="utf-8") as f:
        cases = json.load(f)

    results = []
    for case in cases:
        try:
            route = await classify_intent(
                case["message"],
                case.get("document_ids"),
                thread_id=None,
            )
            correct = route.intent == case["expected_mode"]
            results.append({
                "id": case["id"],
                "message": case["message"],
                "expected": case["expected_mode"],
                "got": route.intent,
                "agent": route.agent_name,
                "correct": correct,
                "note": case.get("note", ""),
            })
        except Exception as exc:
            logger.warning("eval case %s failed: %s", case.get("id"), exc)
            results.append({
                "id": case.get("id", "?"),
                "message": case.get("message", ""),
                "expected": case.get("expected_mode", "?"),
                "got": "error",
                "agent": "error",
                "correct": False,
                "note": str(exc),
            })

    total = len(results)
    correct_count = sum(1 for r in results if r["correct"])
    wrong = [r for r in results if not r["correct"]]

    return {
        "total": total,
        "correct": correct_count,
        "accuracy": round(correct_count / total, 3) if total > 0 else 0,
        "wrong_cases": wrong,
        "all_results": results,
    }


if __name__ == "__main__":
    report = asyncio.run(run_eval_suite())
    print(f"Accuracy: {report['accuracy'] * 100:.1f}% ({report['correct']}/{report['total']})")
    if report["wrong_cases"]:
        print("\nFailed cases:")
        for r in report["wrong_cases"]:
            print(f"  [{r['id']}] expected={r['expected']} got={r['got']} | {r['message'][:60]}")
