"""Crisis assessment, safety planning, and resource knowledge base."""

CRISIS_RISK_LEVELS = {
    "critical": {
        "description": (
            "Immediate danger. The user has expressed active intent or plan to end their life, "
            "has access to means, or is in a psychotic episode. "
            "DO NOT provide ongoing support — route to emergency resources only."
        ),
        "ai_behavior": [
            "Acknowledge the crisis briefly and directly.",
            "Do not explore feelings, offer coping skills, or attempt to counsel.",
            "Provide crisis lines immediately and explicitly.",
            "Encourage contacting emergency services (911/112) or going to the nearest ER.",
            "If means are mentioned, specifically encourage means restriction.",
            "Offer to stay engaged: 'I will be here while you reach out to a crisis line.'",
        ],
        "required_resources": [
            "988 Suicide and Crisis Lifeline (US): Call or text 988",
            "Crisis Text Line: Text HOME to 741741",
            "Emergency Services: 911 or local equivalent",
            "International Association for Suicide Prevention: https://www.iasp.info/resources/Crisis_Centres/",
        ],
    },
    "high": {
        "description": (
            "Significant risk. The user expresses passive suicidal ideation, hopelessness, "
            "or has a history of attempts. No active plan identified but risk is elevated."
        ),
        "ai_behavior": [
            "Acknowledge the pain and validate the difficulty of what they are experiencing.",
            "Do not minimize or argue against hopelessness — listen first.",
            "Conduct a brief safety check: access to means, any specific plan, timeline.",
            "Create or review a safety plan if appropriate.",
            "Encourage professional evaluation and real-world support immediately.",
            "Provide crisis resources proactively.",
            "Set clear boundaries about what this AI can and cannot do in a crisis.",
        ],
        "required_resources": [
            "988 Suicide and Crisis Lifeline (US): Call or text 988",
            "Crisis Text Line: Text HOME to 741741",
            "Encourage: schedule an appointment with a mental health professional within 24-48 hours",
            "If they have a therapist, encourage calling the therapist's after-hours line",
        ],
    },
    "elevated": {
        "description": (
            "Moderate concern. The user expresses emotional distress, self-harm urges, "
            "or passive thoughts of not wanting to live, but without active plan or means."
        ),
        "ai_behavior": [
            "Acknowledge and validate the distress without pathologizing.",
            "Normalize help-seeking behavior.",
            "Assess for protective factors: social support, reasons for living, coping skills.",
            "Introduce distress tolerance skills appropriate to their state.",
            "Encourage connecting with a professional.",
            "Provide crisis resources as backup.",
            "Follow up: ask about their safety at the next interaction.",
        ],
        "recommended_skills": [
            "DBT TIPP skills if acute distress",
            "5-4-3-2-1 grounding if dissociation",
            "Safety planning template",
        ],
    },
    "low": {
        "description": (
            "The user is experiencing normal distress, mild-to-moderate mental health symptoms, "
            "or life challenges that do not indicate acute crisis risk."
        ),
        "ai_behavior": [
            "Continue with standard support, psychoeducation, or intervention approach.",
            "Offer appropriate skills and exercises.",
            "Normalize that struggling is part of being human.",
            "If symptoms suggest professional evaluation might help, gently suggest it.",
        ],
    },
}


SAFETY_PLAN_TEMPLATE = {
    "name": "Personal Safety Plan",
    "description": (
        "A collaborative document created with the user to identify warning signs, "
        "coping strategies, people who can provide support, professional contacts, "
        "means restriction, and reasons for living. "
        "This is NOT a no-harm contract — it is a practical tool."
    ),
    "sections": [
        {
            "step": 1,
            "title": "Warning Signs",
            "description": (
                "What personal warning signs indicate I am entering a crisis? "
                "These might be thoughts, feelings, behaviors, or situations."
            ),
            "prompt": "List 3-5 warning signs that indicate you may be heading toward a crisis. "
            "Be as specific as possible.",
        },
        {
            "step": 2,
            "title": "Coping Strategies",
            "description": (
                "What can I do on my own to get through this without reaching out to others? "
                "Include both distraction strategies and distress tolerance skills."
            ),
            "prompt": "List 3-5 things you can do by yourself to cope with strong emotions. "
            "Prioritize things that are healthy and accessible.",
        },
        {
            "step": 3,
            "title": "People Who Can Provide Support",
            "description": (
                "Who are the people in my life I can reach out to when I am struggling? "
                "Include their name, relationship, phone number, and when to contact them."
            ),
            "prompt": "List 3-5 people who could support you during a difficult time. "
            "Write their name, how they can help, and the best way to reach them.",
        },
        {
            "step": 4,
            "title": "Professional Contacts",
            "description": (
                "Who are the professionals I can contact during a crisis? "
                "Include therapists, psychiatrists, crisis lines, and urgent care."
            ),
            "prompt": "List your therapist, psychiatrist, or primary care provider. "
            "Include their contact information and office hours.",
        },
        {
            "step": 5,
            "title": "Making the Environment Safe",
            "description": (
                "How can I reduce access to lethal means (weapons, medications, etc.) during moments of crisis?"
            ),
            "prompt": "If you have thought about ways to harm yourself, "
            "what can you do to make those means less accessible? "
            "Consider: lock away medications, ask someone to hold onto dangerous items, "
            "remove access temporarily.",
        },
        {
            "step": 6,
            "title": "Reasons for Living",
            "description": (
                "What are the things in my life that are important to me, "
                "that give me reasons to stay alive, even in the darkest moments?"
            ),
            "prompt": "List the people, pets, responsibilities, values, or goals "
            "that give you reasons to keep living, even when things are very hard.",
        },
    ],
}


MEANS_RESTRICTION_GUIDANCE = {
    "description": (
        "Means restriction is one of the most effective suicide prevention strategies. "
        "When someone in crisis has access to lethal means, encouraging temporary removal "
        "of those means significantly reduces suicide risk."
    ),
    "common_means": {
        "medications": {
            "guidance": (
                "Encourage giving medications to a trusted person to hold. "
                "Consider requesting a limited supply from a pharmacy. "
                "Lock medications in a separate location. "
                "If overdose was via prescription, contact prescriber about reducing quantity."
            ),
        },
        "firearms": {
            "guidance": (
                "Encourage temporarily storing firearms with a friend, family member, or law enforcement. "
                "Remove from the home during the crisis period. "
                "Remove ammunition separately. "
                "If this feels like too big a step, even temporarily locking the gun in a different location helps."
            ),
        },
        "sharp_objects": {
            "guidance": (
                "Encourage giving sharp objects to a trusted person to hold. "
                "Consider removing them from the home entirely during the crisis. "
                "Remove the most commonly used item first."
            ),
        },
        "heights": {
            "guidance": (
                "Identify specific locations that are high-risk. "
                "Avoid those locations during crisis periods. "
                "Arrange to have someone check in before those times."
            ),
        },
        "strangulation": {
            "guidance": (
                "Remove items that could be used for self-strangulation from the environment. "
                "This includes certain types of clothing, cords, and other items."
            ),
        },
    },
}


CRISIS_FOLLOWUP_PROTOCOL = {
    "description": (
        "For users who have been through a crisis interaction, follow-up interactions should be handled with care."
    ),
    "check_ins": [
        "Start by asking how they are doing today, specifically regarding their safety.",
        "Ask if they were able to reach out to any of the resources provided.",
        "Inquire about their safety plan — did they complete it? Do they have their support people available?",
        "If they are stable, acknowledge their effort to stay safe.",
        "If they are still in crisis, repeat the crisis resources and encourage professional help.",
        "Do not pressure them to 'feel better' — focus on safety and the next 24 hours.",
        "If they remain high-risk across multiple interactions, explicitly recommend professional treatment.",
    ],
}


def get_safety_plan_template() -> dict:
    return SAFETY_PLAN_TEMPLATE


def get_crisis_resources(level: str) -> list[str]:
    level_data = CRISIS_RISK_LEVELS.get(level, {})
    return level_data.get("required_resources", [])


def get_crisis_ai_behavior(level: str) -> list[str]:
    level_data = CRISIS_RISK_LEVELS.get(level, {})
    return level_data.get("ai_behavior", [])


def get_means_restriction_guidance(means: str) -> str | None:
    return MEANS_RESTRICTION_GUIDANCE.get("common_means", {}).get(means, {}).get("guidance")
