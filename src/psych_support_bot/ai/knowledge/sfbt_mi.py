"""Solution-Focused Brief Therapy (SFBT) and Motivational Interviewing (MI) knowledge base."""

SFBT_TOOLS = {
    "miracle_question": {
        "name": "The Miracle Question",
        "description": (
            "A powerful SFBT question that helps users articulate a preferred future "
            "when they are stuck in problem-talk. "
            "It bypasses the tendency to focus on problems and opens the door to imagining solutions."
        ),
        "script": [
            (
                "Suppose tonight while you are sleeping, a miracle happens. "
                "The problem you are struggling with is solved. "
                "But because you were asleep, you do not know the miracle happened. "
                "When you wake up tomorrow morning, what would be different? "
                "How would you notice that your life is now better?"
            ),
            "Give the user time to describe in detail what the morning would look like if the miracle happened.",
            "Follow-up: What else would be different? (Use this 2-3 times to draw out the full picture.)",
            "Then: How does this miracle-scene relate to your current situation? What small step is already in that direction?",
        ],
        "tips": (
            "If the user says 'that is impossible,' gently redirect: I know it feels that way. "
            "Just suppose it could. What would be different? "
            "Do not argue with the impossibility. Stay in the hypothetical."
        ),
        "when_to_use": "When the user is stuck in problem analysis, expressing hopelessness, "
        "or unable to identify goals.",
    },
    "scaling_questions": {
        "name": "Scaling Questions",
        "description": (
            "Help users rate their situation or motivation on a 0-10 scale. "
            "This creates a concrete way to discuss abstract concepts and track change."
        ),
        "variations": [
            {
                "type": "current_state",
                "prompt": (
                    "On a scale of 0 to 10, where 0 is the worst things have ever been "
                    "and 10 is the best things could be, where are you right now?"
                ),
            },
            {
                "type": "motivation",
                "prompt": "On a scale of 0 to 10, how motivated are you to make a change right now?",
            },
            {
                "type": "confidence",
                "prompt": "On a scale of 0 to 10, how confident are you that you could make this change?",
            },
            {
                "type": "progress",
                "prompt": (
                    "Last time we talked, you rated yourself a 4. Today you say 5 or 6. "
                    "What changed? What did you do differently?"
                ),
            },
        ],
        "follow_up_script": [
            ("Why are you at [number] and not a lower number? This highlights strengths and resources already in use."),
            ("What would it take to get to [number + 1]? This identifies a specific, achievable next step."),
            (
                "If you moved from [current] to [higher], what would be different? "
                "This clarifies what change looks like concretely."
            ),
        ],
        "when_to_use": "Goal-setting, motivation assessment, tracking progress, building confidence before action.",
    },
    "exception_finding": {
        "name": "Exception-Finding Questions",
        "description": (
            "Help users identify times when the problem was less severe or absent. "
            "These 'exceptions' reveal existing skills and solutions that are already working."
        ),
        "types": [
            {
                "type": "times_past",
                "prompt": (
                    "Can you think of a time recently — maybe within the past week or month — "
                    "when the problem was less severe or not present at all?"
                ),
            },
            {
                "type": "times_different",
                "prompt": "What was different about those times? What were you doing differently?",
            },
            {
                "type": "contrast",
                "prompt": (
                    "How did you feel about yourself during those better times? "
                    "What did other people notice that was different?"
                ),
            },
            {
                "type": "pattern",
                "prompt": (
                    "Is this a pattern? Have there been other times like this? What do those times have in common?"
                ),
            },
        ],
        "principle": (
            "The user is the expert on their own life. "
            "Exception-finding questions help them discover solutions that have already worked, "
            "rather than having the AI prescribe solutions from outside."
        ),
        "when_to_use": (
            "When users feel stuck, hopeless, or unable to identify strengths. "
            "Especially useful for depression and low self-efficacy."
        ),
    },
    "coping_questions": {
        "name": "Coping Questions",
        "description": (
            "Acknowledge difficulty while highlighting the user's resilience and resourcefulness. "
            "Especially important for users who feel they are not doing enough."
        ),
        "examples": [
            "Given everything you are going through, how are you managing to keep going?",
            "Most people in this situation would have given up. What keeps you going?",
            "This sounds incredibly hard. How have you been handling this so far?",
            "Despite feeling this way, what is one thing you have done recently that mattered?",
        ],
        "principle": (
            "Coping questions do not minimize the problem or suggest the user should be coping better. "
            "They recognize genuine struggle and implicitly affirm the user's effort."
        ),
        "when_to_use": "Depression, burnout, grief, moments of despair, when the user expresses hopelessness.",
    },
    "relationship_questions": {
        "name": "Relationship Questions",
        "description": (
            "Ask how other people in the user's life would notice or describe a change. "
            "This external perspective can reveal what matters most."
        ),
        "examples": [
            "If your best friend were here and saw this change, what would they say they noticed?",
            "How would your partner, parent, or colleague describe the difference between now and then?",
            "What would the person who knows you best say has changed?",
            "If your younger self could see what you have accomplished, what would they say?",
        ],
        "when_to_use": "When users have difficulty seeing their own progress or identifying what matters to them.",
    },
}


MI_TOOLS = {
    "OARS": {
        "name": "OARS: Core MI Communication Skills",
        "description": (
            "The foundational micro-skills of Motivational Interviewing. "
            "These four skills create the conditions for change talk to emerge."
        ),
        "skills": {
            "O": {
                "name": "Open Questions",
                "description": (
                    "Questions that invite expansive, narrative answers rather than yes/no responses. "
                    "Open questions demonstrate interest and allow the user to share their own perspective."
                ),
                "examples": [
                    "What brings you here today?",
                    "What would you like to be different in your life?",
                    "How has this been affecting you?",
                    "What matters most to you about making this change?",
                ],
                "tips": "Use Affirmations before and after asking heavy questions to build safety.",
            },
            "A": {
                "name": "Affirmations",
                "description": (
                    "Statements that recognize the user's strengths, efforts, and positive qualities. "
                    "Affirmations build confidence and reinforce the user's capacity for change."
                ),
                "examples": [
                    "The fact that you came here today shows real courage.",
                    "You have clearly thought about this carefully.",
                    "Despite how hard this has been, you are still here.",
                    "You have been able to do hard things before.",
                ],
                "tips": "Be genuine. Avoid false praise. Focus on specific efforts and strengths.",
            },
            "R": {
                "name": "Reflections",
                "description": (
                    "The core MI skill. A guess at the meaning behind what the user says. "
                    "Reflections are not restatements — they are active attempts to understand the deeper meaning. "
                    "Reflections should be slightly deeper than what was said."
                ),
                "levels": [
                    "Simple reflection: mirrors what was said. 'You feel sad.'",
                    (
                        "Complex reflection: adds meaning or emotion not explicitly stated. "
                        "'You feel sad, and underneath that, maybe some disappointment about how things turned out.'"
                    ),
                    (
                        "Double-sided reflection: reflects both change talk and sustain talk together. "
                        "'Part of you is ready for this, and part of you is scared.'"
                    ),
                ],
                "tips": [
                    "When uncertain, reflect rather than interpret.",
                    "Reflections build empathy and trust. They show you are listening.",
                    "Incorrect reflections are okay — the user will correct you, which deepens understanding.",
                    "Excessive questions interrupt reflection. Let silence work.",
                ],
            },
            "S": {
                "name": "Summaries",
                "description": (
                    "A collection of reflections that bring together multiple pieces of what the user has shared. "
                    "Summaries signal that you have been listening and organize the conversation."
                ),
                "types": [
                    (
                        "Collecting summary: gathers multiple points from the conversation. "
                        "'Let me make sure I understand: you have been struggling with... "
                        "and what matters most to you is...'"
                    ),
                    (
                        "Linking summary: connects different threads. "
                        "'You mentioned feeling stuck at work, but also said you have made progress with your sleep. "
                        "How do you see those as connected?'"
                    ),
                    (
                        "Transition summary: signals movement in the conversation. "
                        "'So, you are saying things have been hard, but you are also noticing some small signs of hope. "
                        "Would it be okay if we talked about what might help?'"
                    ),
                ],
            },
        },
    },
    "change_talk": {
        "name": "Recognizing and Eliciting Change Talk",
        "description": (
            "Change talk is user-generated language that favors movement toward a goal. "
            "MI strategies specifically elicit and reinforce change talk because it predicts behavior change."
        ),
        "types": {
            "DESIRE": ("I want, I wish, I would like. Example: 'I wish I could stop feeling this anxious.'"),
            "ABILITY": ("I could, I am able to, I can. Example: 'I could try going to bed earlier.'"),
            "REASON": ("Because, the reason is. Example: 'I want to feel better because I am missing out on my life.'"),
            "NEED": ("I need to, I must, I have to. Example: 'I need to do something about this.'"),
            "COMMITMENT": ("I will, I intend to, I plan to. Example: 'I will try that tomorrow.'"),
            "ACTIVATION": ("I am ready, I am willing. Example: 'I am willing to give it a try.'"),
            "TAKING_STEPS": ("I have, I did. Example: 'I have been going for walks every morning this week.'"),
        },
        "sustain_talk": (
            "Sustain talk is language that favors the status quo. "
            "It is not resistance — it is ambivalence. "
            "Responding to sustain talk with argumentation or pressure usually increases it. "
            "Instead: reflect the sustain talk, then invite exploration of the other side."
        ),
        "MI_adherent": (
            "MI-adherent responses are those that support autonomy (asking permission, emphasizing choice), "
            "provide empathy (reflections), and support the user's own reasons for change. "
            "MI-non-adherent responses (confronting, directing, warning) tend to increase sustain talk."
        ),
        "strategies": [
            "Ask evocative questions: 'What makes you think you might want to change?'",
            "Use importance ruler: 'On a scale of 1-10, how important is this change to you?'",
            (
                "Explore the user's values: 'You mentioned family is really important to you. "
                "How does your current behavior connect to that?'"
            ),
            (
                "Use reflections to deepen change talk: "
                "User: 'I guess I should exercise more.' -> "
                "Reflection: 'You are noticing that movement might help, and you value taking care of yourself.'"
            ),
            (
                "Exaggerate slightly to invite correction: 'So, nothing would have to change?' "
                "The user may then correct: 'Well, I guess some things would have to change...'"
            ),
        ],
    },
    "developing_discrepancy": {
        "name": "Developing Discrepancy",
        "description": (
            "Helps the user become aware of the gap between their current behavior "
            "and their broader goals, values, or self-image. "
            "This gap creates motivation for change without external pressure."
        ),
        "script": [
            "Explore current behavior: 'Tell me how things are going with [behavior].'",
            "Explore values and goals: 'Earlier you mentioned [value]. Tell me more about that.'",
            (
                "Draw the connection: 'So your work with [value] is important to you, "
                "and [current behavior] is making that harder. How do you see those together?'"
            ),
            ("Let the user articulate the discrepancy: 'What do you make of that?' or 'How do you feel about that?'"),
        ],
        "principle": (
            "Do not lecture or confront. The AI should not be the one pointing out the contradiction. "
            "The goal is for the user to notice and articulate the discrepancy themselves."
        ),
        "when_to_use": "Ambivalence about change, when the user seems aware of the problem but is stuck.",
    },
}


def get_sfbt_tool(tool_id: str) -> dict | None:
    return SFBT_TOOLS.get(tool_id)


def get_mi_skill(skill_id: str) -> dict | None:
    return MI_TOOLS.get("OARS", {}).get("skills", {}).get(skill_id)


def get_mi_concept(concept_id: str) -> dict | str | None:
    return MI_TOOLS.get(concept_id) or MI_TOOLS.get("change_talk", {}).get(concept_id)


def list_sfbt_tools() -> dict[str, str]:
    return {k: v["name"] for k, v in SFBT_TOOLS.items()}
