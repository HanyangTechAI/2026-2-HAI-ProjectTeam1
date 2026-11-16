"""Allocate information topics before rendering task content."""

from collections import Counter
from collections.abc import Sequence


TOPICS = ("constraint", "temporal", "state", "flexible")
PROFILE = "topic_balanced_v3"


def validate_topics(topics: Sequence[str]) -> tuple[str, ...]:
    if isinstance(topics, (str, bytes)) or not isinstance(topics, Sequence):
        raise ValueError("dataset.topics must be a nonempty list of information topics")
    values = tuple(topics)
    if not values or any(value not in TOPICS for value in values) or len(set(values)) != len(values):
        raise ValueError(f"dataset.topics must contain unique values from {TOPICS}")
    return values


def plan_cases(
    seeds: Sequence[int], samples_per_seed: int, task_types: Sequence[str],
    topics: Sequence[str] = TOPICS,
) -> list[dict[str, int | str]]:
    """Balance topics first and task families inside each topic across seeds."""
    topics = validate_topics(topics)
    counts = Counter()
    plan = []
    for seed_position, seed in enumerate(seeds):
        for sample_index in range(samples_per_seed):
            ordinal = seed_position * samples_per_seed + sample_index
            topic = topics[(ordinal + seeds[0]) % len(topics)]
            topic_index = counts[topic]
            counts[topic] += 1
            task_type = task_types[topic_index % len(task_types)]
            plan.append(dict(
                seed=seed, sample_index=sample_index, primary_topic=topic,
                topic_index=topic_index, topic_variant_index=topic_index // len(task_types), task_type=task_type,
                state_scope="historical" if (topic_index // len(task_types) + topic_index) % 2 else "current",
                approval_status="pending" if (topic_index // len(task_types)) % 2 == 0 else "granted",
            ))
    return plan
