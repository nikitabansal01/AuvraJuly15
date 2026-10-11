"""Read recorded check-in factors without treating missing scores as zero."""
from typing import Iterable


def factor_labels(values) -> list[str]:
    if not isinstance(values, list):
        return []
    labels = {}
    for value in values:
        if not isinstance(value, str):
            continue
        label = " ".join(value.replace("_", " ").split())
        key = label.casefold()
        if key and key not in {"none", "none of the above", "not sure"}:
            labels.setdefault(key, label)
    return list(labels.values())


def factor_mentions(checkins: Iterable) -> dict:
    checkins = list(checkins)
    result = {"helps": [], "hurts": []}
    for direction, field, score_field, average_field in (
        ("helps", "factors_positive", "overall_wellbeing", "avg_wellbeing"),
        ("hurts", "factors_negative", "concern_severity", "avg_severity"),
    ):
        factors = {}
        for checkin in checkins:
            score = getattr(checkin, score_field, None)
            score_valid = type(score) is int and 1 <= score <= 9
            for label in factor_labels(getattr(checkin, field, None)):
                record = factors.setdefault(label.casefold(), {"factor": label, "occurrences": 0, "scores": []})
                # A mention exists even when this flow did not collect a score.
                record["occurrences"] += 1
                if score_valid:
                    record["scores"].append(score)
        for record in factors.values():
            scores = record.pop("scores")
            record["rated_occurrences"] = len(scores)
            record[average_field] = round(sum(scores) / len(scores), 1) if scores else None
            result[direction].append(record)
        result[direction].sort(key=lambda item: (-item["occurrences"], item["factor"].casefold()))
    return result
