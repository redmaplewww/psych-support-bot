from collections.abc import Mapping
from dataclasses import asdict, is_dataclass
from typing import Any

from psych_support_bot.ai.knowledge.act import ACT_EXERCISES as KNOWLEDGE_ACT_EXERCISES
from psych_support_bot.ai.knowledge.cbt import CBT_EXERCISES as KNOWLEDGE_CBT_EXERCISES
from psych_support_bot.ai.knowledge.dbt import DBT_EXERCISES as KNOWLEDGE_DBT_EXERCISES

CBT_EXERCISES = {
    "thought_record": {
        "name": "CBT Thought Record",
        "description": "Identify and reframe distorted automatic thoughts.",
        "steps": [
            "Step 1: Situation. Briefly describe what happened.",
            "Step 2: Emotion. Name the emotion(s) and rate intensity 0-100.",
            "Step 3: Automatic thought. What went through your mind in that moment?",
            "Step 4: Cognitive distortion. Is this mind-reading, catastrophizing, all-or-nothing, or fortune-telling?",
            "Step 5: Balanced thought. Is there another way to look at this?",
            "Step 6: Revised emotion. How intense is the emotion now, 0-100?",
        ],
        "output_format": "Respond with one step at a time. Ask for the user's answer before moving to the next step.",
    },
    "behavioral_activation": {
        "name": "Behavioral Activation",
        "description": "Break the cycle of withdrawal and low mood through small valued actions.",
        "steps": [
            "Step 1: Mood check-in. Rate your current mood 0-10.",
            "Step 2: Identify avoidance. What have you been avoiding recently?",
            "Step 3: List one small activity you could do in the next hour, even at low energy.",
            "Step 4: Anticipate obstacles. What might stop you? How will you manage that?",
            "Step 5: Schedule it. When exactly will you do it?",
            "Step 6: After-action review. Did you do it? How did your mood change?",
        ],
        "output_format": "Guide through one step per exchange. Keep encouragement brief and specific.",
    },
}

ACT_EXERCISES = {
    "defusion": {
        "name": "ACT Cognitive Defusion",
        "description": "Step back from painful thoughts rather than being fused with them.",
        "steps": [
            "Step 1: Notice the thought. What thought is looping or distressing you?",
            "Step 2: Add a prefix. Say it with: 'I am having the thought that...'",
            "Step 3: Sing it. Silently sing the thought to a familiar tune.",
            "Step 4: Thank your mind. Say 'Thanks, mind' and gently return attention.",
            "Step 5: Ask: Is this thought helpful right now? Does acting on it align with my values?",
        ],
        "output_format": "Guide one step at a time. After the user tries a step, briefly reflect what you notice.",
    },
    "values_clari": {
        "name": "ACT Values Clarification",
        "description": "Identify what matters most to guide meaningful action.",
        "steps": [
            "Step 1: Life areas. Consider: relationships, health, growth, contribution.",
            "Step 2: Pick one area that matters most to you right now.",
            "Step 3: Describe what a day fully aligned with that value would look like.",
            "Step 4: Rate how close you are to living that day, 0-10.",
            "Step 5: Name one small action this week that moves toward that value.",
        ],
        "output_format": "Ask one question at a time. Reflect back the user's words to show you heard them.",
    },
}

DBT_EXERCISES = {
    "tipp": {
        "name": "DBT TIPP Skills for Crisis",
        "description": "Rapid physiological calming for intense emotional states.",
        "steps": [
            "T: Temperature. Hold something cold on your face for 10-30 seconds.",
            "I: Intense exercise. Do 10-20 jumping jacks or run in place.",
            "P: Paced breathing. Breathe in for 4 counts, out for 8 counts. Repeat 5 times.",
            "P: Progressive muscle relaxation. Tense then release each muscle group for 5 seconds.",
        ],
        "output_format": "State each skill step clearly and briefly. Confirm the user has tried it before moving on.",
    },
    "wise_mind": {
        "name": "DBT Wise Mind",
        "description": "Access the balance between emotional intuition and logical analysis.",
        "steps": [
            "Step 1: State the facts. What objective facts can you list about the situation?",
            "Step 2: State the emotion. What is the emotion telling you?",
            "Step 3: Find the overlap. Where do the facts and emotion both point?",
            "Step 4: Ask: Does this wise mind insight suggest a next step?",
        ],
        "output_format": "Guide through each step. Reflect back key phrases from the user.",
    },
}

SLEEP_HYGIENE = {
    "wind_down": {
        "name": "Sleep Wind-Down Routine",
        "description": "Create a consistent pre-sleep ritual to signal your body it is time to rest.",
        "steps": [
            "Step 1: Set a cutoff. Choose a time tonight to stop screens and work.",
            "Step 2: Dim the lights. Lower lighting 30-60 minutes before bed.",
            "Step 3: Choose one calming activity: reading, gentle stretching, or breathing.",
            "Step 4: Keep the room cool and dark. Remove visible clocks.",
            "Step 5: If racing thoughts arise, write them on a notepad and set them aside.",
        ],
        "output_format": "Walk through each step. Ask the user to pick one to start tonight.",
    },
}

PANIC_STABILIZATION = {
    "grounding_5_4_3_2_1": {
        "name": "5-4-3-2-1 Grounding",
        "description": "Use your senses to anchor to the present moment during a panic or dissociation.",
        "steps": [
            "Name 5 things you can see around you right now.",
            "Name 4 things you can physically feel.",
            "Name 3 things you can hear.",
            "Name 2 things you can smell.",
            "Name 1 thing you can taste.",
        ],
        "output_format": "Prompt each step clearly and wait for the user's response before the next step.",
    },
}


def _normalize_exercise(exercise: Any) -> dict[str, Any]:
    if is_dataclass(exercise):
        normalized = asdict(exercise)
        normalized.setdefault(
            "output_format",
            "Guide one step at a time and pause for the user's response before continuing.",
        )
        return normalized
    if isinstance(exercise, Mapping):
        return dict(exercise)
    raise TypeError("Unsupported exercise format")


def _knowledge_exercises() -> dict[str, dict[str, Any]]:
    return {
        **{f"cbt_{key}": _normalize_exercise(value) for key, value in KNOWLEDGE_CBT_EXERCISES.items()},
        **{f"act_{key}": _normalize_exercise(value) for key, value in KNOWLEDGE_ACT_EXERCISES.items()},
        **{f"dbt_{key}": _normalize_exercise(value) for key, value in KNOWLEDGE_DBT_EXERCISES.items()},
    }


def get_exercise_by_tag(tag: str) -> dict[str, Any] | None:
    all_exercises = {
        **{f"cbt_{k}": v for k, v in CBT_EXERCISES.items()},
        **{f"act_{k}": v for k, v in ACT_EXERCISES.items()},
        **{f"dbt_{k}": v for k, v in DBT_EXERCISES.items()},
        **{f"sleep_{k}": v for k, v in SLEEP_HYGIENE.items()},
        **{f"panic_{k}": v for k, v in PANIC_STABILIZATION.items()},
        **_knowledge_exercises(),
    }
    return all_exercises.get(tag)


def list_all_exercises() -> dict[str, list[str]]:
    knowledge = _knowledge_exercises()
    return {
        "cbt": sorted(
            {
                *(CBT_EXERCISES.keys()),
                *(key.removeprefix("cbt_") for key in knowledge if key.startswith("cbt_")),
            }
        ),
        "act": sorted(
            {
                *(ACT_EXERCISES.keys()),
                *(key.removeprefix("act_") for key in knowledge if key.startswith("act_")),
            }
        ),
        "dbt": sorted(
            {
                *(DBT_EXERCISES.keys()),
                *(key.removeprefix("dbt_") for key in knowledge if key.startswith("dbt_")),
            }
        ),
        "sleep": list(SLEEP_HYGIENE.keys()),
        "panic": list(PANIC_STABILIZATION.keys()),
    }
