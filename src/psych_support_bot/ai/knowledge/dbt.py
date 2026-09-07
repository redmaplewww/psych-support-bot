"""Comprehensive DBT (Dialectical Behavior Therapy) knowledge base."""

DBT_MODULES = {
    "distress_tolerance": {
        "description": (
            "Skills for tolerating emotional crises without making them worse. "
            "These skills are used when a problem cannot be solved and must be endured."
        ),
        "skills": {
            "TIPP": {
                "name": "TIPP: Temperature, Intense Exercise, Paced Breathing, Progressive Relaxation",
                "description": "Rapid physiological calming for acute emotional crises. "
                "These skills change the body's state directly, bypassing the need for cognitive change.",
                "steps": [
                    "Temperature: Apply cold to the face. Hold an ice cube, splash cold water on face, "
                    "or use a cold pack on eyes and cheeks for 10-30 seconds. "
                    "This activates the dive reflex, slowing heart rate.",
                    "Intense Exercise: Do 10-20 jumping jacks, run in place, or climb stairs. "
                    "Vigorous movement burns off stress chemicals and changes neurochemistry.",
                    "Paced Breathing: Breathe in for 4 counts, out for 8 counts. "
                    "The extended exhale activates the parasympathetic nervous system.",
                    "Progressive Muscle Relaxation: Tense each muscle group for 5 seconds, then release. "
                    "Start with feet and work up to the face. Notice the difference between tension and relaxation.",
                ],
                "when_to_use": "Acute emotional crisis, overwhelming urge to self-harm, panic attack, extreme anger.",
            },
            "ACCEPTS": {
                "name": "ACCEPTS: Distraction Skills",
                "description": "Distraction techniques to survive emotional crises without making decisions impulsively.",
                "activities": "Engage in an activity that absorbs attention: exercise, work, organize something, "
                "help someone.",
                "contributing": "Do something kind for others. Volunteer, write a thank-you note, perform a small act.",
                "comparisons": "Compare yourself to someone doing worse, or to how others have survived similar pain.",
                "emotions": "Deliberately evoke an opposite emotion: watch something funny, watch something sad "
                "(to cry), listen to calming music.",
                "pushing_away": "Create psychological distance. Put the problem aside for now. "
                "You will come back to it later.",
                "thoughts": "Engage the mind: do a puzzle, memorize something, count something, read.",
                "sensations": "Use one strong sensation: hold ice, take a hot shower, grip a rubber ball, "
                "bite into a lemon.",
            },
            "radical_acceptance": {
                "name": "Radical Acceptance",
                "description": (
                    "Fully accepting reality as it is, even when it is painful, without fighting, "
                    "resisting, or judging it. This does not mean approving or giving up. "
                    "It means stopping the additional suffering that comes from non-acceptance."
                ),
                "steps": [
                    "Acknowledge that the situation is what it is. "
                    "Notice if you are fighting the reality of this moment.",
                    "Remind yourself: 'This is what happened. I cannot change the past.' "
                    "Fighting reality is exhausting and ineffective.",
                    "Notice the urge to say 'but it should not be this way.' "
                    "Let that urge pass. It does not change reality.",
                    "Notice body sensations that arise with acceptance versus non-acceptance. "
                    "Acceptance usually brings physical relaxation and mental calm.",
                    "Repeat internally: 'This is the reality I am in. It is painful. "
                    "I can accept it and move forward.'",
                ],
                "common_resistance": [
                    "Thinking 'If I accept this, it means I am okay with it.' (Acceptance is not approval.)",
                    "Thinking 'Acceptance means nothing will change.' (Acceptance enables effective action.)",
                    "Thinking 'They should not have done this.' (True, but they did. Reality is the starting point.)",
                ],
            },
            "IMPROVE": {
                "name": "IMPROVE: Self-Soothing and Coping",
                "description": "Build a personalized crisis toolkit using these categories.",
                "components": {
                    "I": "Imagery. Visualize a peaceful, safe place in vivid detail. Engage all senses.",
                    "M": "Meaning. Find meaning in the pain. What might you learn? How might this strengthen you?",
                    "P": "Prayer. Whatever belief system resonates: prayer, meditation, or connection to something larger.",
                    "R": "Relaxation. Use deep breathing, progressive muscle relaxation, or a warm bath.",
                    "O": "One thing in the moment. Focus on doing just one thing at a time. "
                    "Do not spiral into the whole situation.",
                    "V": "Vacation. Give yourself a short mental vacation. "
                    "A brief break from the situation, even in your mind.",
                    "E": "Encouragement. Talk to yourself like an encouraging friend. "
                    "What would you say to someone you care about in this situation?",
                },
            },
        },
    },
    "emotion_regulation": {
        "description": (
            "Skills for understanding and changing emotions. The goal is not to suppress emotions "
            "but to reduce vulnerability to emotional suffering and increase the ability to regulate emotional states."
        ),
        "skills": {
            "identify_emotions": {
                "name": "Identifying and Naming Emotions",
                "description": (
                    "Many people struggle to name what they feel beyond 'bad' or 'upset.' "
                    "Precise labeling of emotions reduces their intensity."
                ),
                "steps": [
                    "Notice physical sensations in the body. Emotions always have a physical component.",
                    "Name the emotion: anger, fear, sadness, shame, guilt, disgust, joy, love, surprise. "
                    "Which category does this fit?",
                    "Rate intensity 0-100.",
                    "Identify secondary emotions. Sometimes the surface emotion hides a deeper one. "
                    "For example, anger often masks hurt or fear.",
                    "Check the 'wave' of the emotion. Emotions rise, peak, and subside. "
                    "They do not stay at peak intensity forever.",
                ],
            },
            "opposite_action": {
                "name": "Opposite Action",
                "description": (
                    "When an emotion does not fit the facts of a situation, or when acting on it would be "
                    "unhelpful or harmful, do the opposite of what the emotion urges."
                ),
                "steps": [
                    "Identify the emotion and the action it is urging.",
                    "Ask: Is acting on this emotion justified by the facts? "
                    "Would this action make the situation better or worse?",
                    "If it would make things worse, identify the opposite action.",
                    "Examples: Feeling like withdrawing but isolation worsens mood — reach out instead. "
                    "Feeling like yelling but it damages relationships — speak calmly. "
                    "Feeling like self-harm but the urge is not based on current danger — use distress tolerance.",
                    "Do the opposite action fully and committedly, even if it feels fake initially.",
                ],
                "cautions": [
                    "This skill is not for emotions that are justified and protective. "
                    "Fear is appropriate when there is real danger.",
                    "Grief and sadness do not have an 'opposite action' — they need to be felt.",
                ],
            },
            "checking_facts": {
                "name": "Check the Facts",
                "description": (
                    "Before reacting to an emotion, check whether the emotion fits the facts. "
                    "This is similar to CBT but integrated into the emotion regulation module."
                ),
                "steps": [
                    "State the situation that triggered the emotion.",
                    "Identify the emotion and intensity.",
                    "Ask: What interpretation or thought triggered this emotion?",
                    "Ask: What facts support this interpretation? What facts contradict it?",
                    "Based on the evidence, revise the interpretation if needed.",
                    "Re-rate the emotion. Has the intensity decreased with a more accurate interpretation?",
                ],
            },
            "emotions_are_waves": {
                "name": "Emotions Are Like Waves",
                "description": (
                    "Emotions naturally rise, peak, and fall — like waves. "
                    "Trying to suppress, avoid, or fight them usually makes them worse or more persistent."
                ),
                "guidance": [
                    "Notice the urge to push the emotion away.",
                    "Remind yourself: waves come and go. This one will pass.",
                    "Let the wave be there. You do not have to act on it.",
                    "Ride the wave with awareness. Notice where it lives in your body.",
                    "If you need to do something, do one opposite action or use distress tolerance.",
                    "Avoid rumination, avoidance, or self-harm as ways of dealing with emotions.",
                ],
            },
        },
    },
    "interpersonal_effectiveness": {
        "description": (
            "Skills for navigating relationships, asserting needs, setting boundaries, and maintaining self-respect. "
            "The core question: how to be effective in getting what you need while maintaining "
            "relationships and self-respect."
        ),
        "skills": {
            "DEAR_MAN": {
                "name": "DEAR MAN: Assertive Communication",
                "description": "A structured approach for asking for something, saying no, or negotiating conflict.",
                "steps": [
                    {"letter": "D", "text": "Describe: Describe the situation factually. 'When you... [fact].'"},
                    {"letter": "E", "text": "Express: Express how you feel about it. 'I feel...'"},
                    {"letter": "A", "text": "Assert: State clearly what you want or need. 'I would like...'"},
                    {
                        "letter": "R",
                        "text": "Reinforce: Explain the benefit of the request. 'This would help because...' or 'I will appreciate it because...'",
                    },
                    {
                        "letter": "M",
                        "text": "Mindful: Stay on topic. Do not get derailed by other issues or manipulations.",
                    },
                    {"letter": "A", "text": "Appear confident: Use steady eye contact, calm posture, clear voice."},
                    {"letter": "N", "text": "Negotiate: Be willing to compromise. Offer alternatives."},
                ],
                "examples": [
                    "Asking for help: 'When I have a lot on my plate, I feel overwhelmed. "
                    "I would like to ask for your help with the presentation. "
                    "This would make a big difference and I will return the favor.'",
                    "Saying no: 'I hear that you need help. I am not able to help right now. "
                    "I can help with X instead.'",
                ],
            },
            "FAST": {
                "name": "FAST: Self-Respect Effectiveness",
                "description": "Skills for maintaining self-respect in interpersonal situations, especially under pressure.",
                "steps": {
                    "F": "Fair to yourself. Do not sacrifice your own needs entirely to please others.",
                    "A": "No Apologies. Apologize once if needed, but not repeatedly. "
                    "Excessive apologizing undermines your position.",
                    "S": "Stick to your values. Ask: What matters most here? "
                    "Act in alignment with your values rather than reactively.",
                    "T": "Truthful. Be honest and direct. Do not exaggerate or manipulate to get your way.",
                },
            },
            "GIVE": {
                "name": "GIVE: Relationship Effectiveness",
                "description": "Skills for maintaining and strengthening relationships, especially when the relationship matters.",
                "steps": {
                    "G": "Gentle. Approach without aggression, threats, or ultimatums.",
                    "I": "Interested. Show genuine interest in the other person's perspective.",
                    "V": "Validate. Acknowledge their feelings, even if you disagree. "
                    "'I can see why you feel that way.'",
                    "E": "Easy manner. Use some humor, a light tone, and an easy manner when possible.",
                },
            },
            "boundary_setting": {
                "name": "Boundary Setting",
                "description": (
                    "Boundaries define what you will and will not accept from others. "
                    "They are not walls — they are fences with gates."
                ),
                "steps": [
                    "Identify the boundary. What specific behavior are you tolerating that you want to stop?",
                    "State the boundary clearly and calmly, without explanation or justification. "
                    "'I need you to stop calling me after 10pm.'",
                    "If the boundary is crossed, restate it once calmly.",
                    "Enforce the consequence if the boundary continues to be crossed.",
                    "Expect discomfort. Setting boundaries often feels uncomfortable at first.",
                    "Practice self-compassion if you feel guilty. Guilt is a common and temporary side effect.",
                ],
            },
        },
    },
    "mindfulness": {
        "description": (
            "Core DBT mindfulness skills that underpin all other modules. "
            "These skills train the ability to be fully present and nonjudgmentally aware of the current moment."
        ),
        "skills": {
            "what_skills": {
                "name": "What Skills: What to Do",
                "description": (
                    "Two primary 'what' skills for mindfulness: observing and describing. "
                    "These skills train the ability to witness experience without being swept up in it."
                ),
                "observe": (
                    "Notice what is happening right now — a sensation, thought, feeling, or sound. "
                    "Try not to label it yet. Just notice it, as if you were a scientist observing data. "
                    "Notice thoughts as they arise and let them pass. Notice sensations as they move through the body."
                ),
                "describe": (
                    "Put words on what you observe. "
                    "If you noticed a thought, label it: 'I am thinking that I am not good enough.' "
                    "If you noticed a sensation, label it: 'I feel tightness in my chest.' "
                    "Labeling creates distance from the experience."
                ),
                "participate": (
                    "Fully enter into the current moment. "
                    "Let go of self-consciousness and engage in what is happening right now."
                ),
            },
            "how_skills": {
                "name": "How Skills: How to Do It",
                "description": "Three 'how' skills that describe the quality of attention.",
                "nonjudgmentally": (
                    "Observe without evaluating as good or bad. "
                    "This is one of the hardest skills because humans naturally judge. "
                    "Simply notice and describe without 'good,' 'bad,' 'right,' or 'wrong.'"
                ),
                "one_mindfully": (
                    "Do one thing at a time with full attention. "
                    "When the mind wanders, gently bring it back. "
                    "This trains focus and reduces the chaos of divided attention."
                ),
                "effectively": (
                    "Do what works, not what is perfect, righteous, or fair. "
                    "Focus on the goal. Sometimes being effective means doing something imperfectly "
                    "rather than doing nothing or doing the 'perfect' thing that fails."
                ),
            },
        },
    },
}


DBT_EXERCISES = {
    "tipp_full": {
        "name": "DBT TIPP Skills — Full Protocol",
        "description": (
            "A complete walkthrough of the TIPP skills for rapid physiological calming during "
            "acute emotional crises. Walk through each skill one at a time."
        ),
        "steps": [
            "Temperature: Find something cold — ice pack, cold water, or ice cube. "
            "Apply it to your cheeks and under your eyes for 10-30 seconds. "
            "This triggers the dive reflex, which slows your heart rate. "
            "Breathe slowly while doing this. Did you feel a shift?",
            "Intense Exercise: Stand up and do 20 jumping jacks, or run in place for 30-60 seconds. "
            "Physical exertion burns off cortisol and adrenaline. "
            "How does your body feel after moving vigorously?",
            "Paced Breathing: Breathe in through your nose for 4 counts. "
            "Breathe out through your mouth for 8 counts. "
            "The extended exhale activates the parasympathetic nervous system (the rest-and-digest system). "
            "Repeat 5 times. Notice your heart rate.",
            "Progressive Muscle Relaxation: Starting with your feet, tense the muscles tightly for 5 seconds, "
            "then release. Work up through calves, thighs, stomach, chest, hands, arms, shoulders, and face. "
            "Notice the difference between tension and relaxation.",
            "Review: After completing all four skills, re-rate your emotional intensity 0-100. "
            "Did the distress reduce? What was most helpful?",
        ],
        "output_format": "Guide through one skill at a time. Wait for the user to try it before moving on.",
        "when_to_use": "Acute emotional crisis, panic attack, overwhelming urge to act destructively.",
    },
    "wise_mind": {
        "name": "DBT Wise Mind",
        "description": (
            "Access the synthesis of emotional wisdom and logical analysis. "
            "Both 'emotion mind' (pure feeling) and 'reasonable mind' (pure logic) are incomplete. "
            "'Wise mind' integrates both."
        ),
        "steps": [
            "State the facts. List the objective facts of the situation. "
            "What happened? What did people actually say and do? Stick to observable facts.",
            "State the emotion. What emotion is driving or present? "
            "What is the emotion telling you about what matters in this situation?",
            "Find the overlap. Where do the facts and emotion both point? "
            "This overlap area is wise mind — the place where reason and feeling agree.",
            "Check: Does this wise mind insight suggest a specific next step? "
            "What does your wise mind say you should do?",
        ],
        "output_format": "Guide through one step at a time. Reflect back key words the user shares.",
        "when_to_use": "Decision-making, emotional conflicts, moments of confusion about what to do.",
    },
    "radical_acceptance_walkthrough": {
        "name": "Radical Acceptance Practice",
        "description": (
            "A guided practice for using radical acceptance when facing an unchangeable painful reality. "
            "This is for situations that cannot be changed — past events, other people's behavior, etc."
        ),
        "steps": [
            "Identify the situation. What reality are you currently facing that you wish were different?",
            "Notice the resistance. Are you thinking 'This should not be this way'? "
            "Notice how exhausting that fight is.",
            "Repeat internally: 'This is what happened. This is the reality I am in.' "
            "Notice if the resistance decreases slightly.",
            "Acknowledge the pain. It is painful. You do not have to pretend it is not. "
            "Allow the feeling of pain to exist without fighting it.",
            "Notice physical changes. Does non-acceptance feel more tense in the body? "
            "Does acceptance feel more relaxed, even in the presence of pain?",
            "Ask: What can I do about this reality right now? "
            "If something, take that action. If nothing, accept that too.",
        ],
        "output_format": "Go slowly through each step. Pause and let the user sit with the discomfort between steps.",
        "when_to_use": "Grief, loss, chronic illness, past trauma, relationship pain that cannot be changed.",
    },
    "dear_man_assertion": {
        "name": "DEAR MAN Assertive Request Practice",
        "description": (
            "Practice using the DEAR MAN framework for making an assertive request or saying no. "
            "Walk through the full framework step by step."
        ),
        "steps": [
            "D — Describe: What is the specific situation you want to address? Describe it factually without judgment.",
            "E — Express: How does this situation make you feel? Name the emotion and its intensity.",
            "A — Assert: What specifically are you asking for, or what are you saying no to? "
            "State it clearly and directly.",
            "R — Reinforce: Why would fulfilling this request benefit both of you? Explain the positive outcome.",
            "M — Mindful: If the conversation gets derailed, how will you bring it back to the topic?",
            "A — Appear confident: Practice maintaining calm eye contact and a steady voice.",
            "N — Negotiate: What are you willing to compromise on? "
            "Is there a middle ground that partially meets both needs?",
        ],
        "output_format": "Help the user fill in each letter step. Then ask them to practice saying it out loud.",
        "when_to_use": "Difficulty asserting needs, people-pleasing, conflict avoidance.",
    },
}


def get_dbt_module(module_id: str) -> dict | None:
    return DBT_MODULES.get(module_id)


def get_dbt_skill(module_id: str, skill_id: str) -> dict | None:
    module = DBT_MODULES.get(module_id)
    if module is None:
        return None
    return module.get("skills", {}).get(skill_id)


def get_dbt_exercise(exercise_id: str) -> dict | None:
    return DBT_EXERCISES.get(exercise_id)


def list_dbt_exercises() -> dict[str, str]:
    return {k: v["name"] for k, v in DBT_EXERCISES.items()}
