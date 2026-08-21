"""Comprehensive CBT knowledge base with techniques, exercises, and intervention scripts."""

from dataclasses import dataclass, field


@dataclass
class CBTExercise:
    exercise_id: str
    name: str
    description: str
    target_symptoms: list[str]
    duration_minutes: int
    steps: list[str]
    user_guidance: str
    contraindications: list[str] = field(default_factory=list)
    follow_up_prompts: list[str] = field(default_factory=list)


@dataclass
class CBTTechnique:
    technique_id: str
    name: str
    description: str
    when_to_use: list[str]
    how_it_works: str
    key_questions: list[str]
    common_distortions: list[str] = field(default_factory=list)


COGNITIVE_DISTORTIONS = {
    "all_or_nothing": {
        "name": "All-or-Nothing Thinking",
        "description": "Viewing situations in only two categories instead of on a continuum.",
        "examples": [
            "If I am not perfect, I am a failure.",
            "Either I do it perfectly or I should not do it at all.",
            "If this one thing goes wrong, everything is ruined.",
        ],
        "reframe": "Most situations exist on a spectrum. There can be partial success, meaningful effort, and learning moments.",
        "challenge_prompt": "Can you think of a middle ground between these two extremes?",
    },
    "catastrophizing": {
        "name": "Catastrophizing",
        "description": "Expecting the worst possible outcome in any situation.",
        "examples": [
            "I will fail this and my life will be over.",
            "If this relationship does not work, I will be alone forever.",
            "This headache must be a tumor.",
        ],
        "reframe": "Even if things go wrong, there are usually ways to cope and recover. The predicted disaster is rarely as bad as it seems.",
        "challenge_prompt": "What is the most likely outcome, not the worst possible one?",
    },
    "mind_reading": {
        "name": "Mind Reading",
        "description": "Assuming you know what others are thinking without evidence.",
        "examples": [
            "They think I am incompetent.",
            "Everyone is judging me.",
            "She must hate me after what I said.",
        ],
        "reframe": "We cannot know what others truly think. Unless someone explicitly tells us, this is speculation.",
        "challenge_prompt": "What evidence do you actually have for what they are thinking?",
    },
    "fortune_telling": {
        "name": "Fortune Telling",
        "description": "Predicting negative outcomes in the future as if they are already certain.",
        "examples": [
            "I know I will embarrass myself at the interview.",
            "This will never work out.",
            "I can tell this is going to be a disaster.",
        ],
        "reframe": "Future events are not predetermined. Acting as if they are removes the possibility of change.",
        "challenge_prompt": "What would you think if this turned out well instead?",
    },
    "emotional_reasoning": {
        "name": "Emotional Reasoning",
        "description": "Using feelings as evidence for facts. 'I feel it, therefore it must be true.'",
        "examples": [
            "I feel anxious, so something bad is about to happen.",
            "I feel worthless, therefore I am worthless.",
            "I feel hopeless, so my situation must be hopeless.",
        ],
        "reframe": "Feelings are signals, not facts. They reflect how we interpret situations, not necessarily the reality of the situation.",
        "challenge_prompt": "If you were not feeling this way right now, what might you think about this situation?",
    },
    "should_statements": {
        "name": "Should Statements",
        "description": "Rigid rules about how things should be, leading to guilt and frustration.",
        "examples": [
            "I should always be productive.",
            "I should never feel angry.",
            "I should be able to handle this without help.",
        ],
        "reframe": "People are human and have limits. 'Would it be better if...' opens options without the pressure of 'should.'",
        "challenge_prompt": "What would you tell a friend in the same situation?",
    },
    "labeling": {
        "name": "Labeling",
        "description": "Attaching a fixed negative identity label to yourself or others based on one behavior.",
        "examples": [
            "I am such a loser.",
            "He is a jerk.",
            "I am completely worthless.",
        ],
        "reframe": "One behavior does not define a person. A person is complex and contains many qualities.",
        "challenge_prompt": "Is this one behavior really your entire identity?",
    },
    "mental_filter": {
        "name": "Mental Filter",
        "description": "Focusing exclusively on negatives while ignoring positives.",
        "examples": [
            "The whole day was terrible because of one thing.",
            "One criticism erases all the praise I received.",
            "Everything about my job is bad.",
        ],
        "reframe": "A balanced view requires considering both positives and negatives. The mental filter distorts the full picture.",
        "challenge_prompt": "What went well today that you might be overlooking?",
    },
    "disqualifying_positives": {
        "name": "Disqualifying the Positive",
        "description": "Rejecting positive experiences by saying they do not count.",
        "examples": [
            "That does not count, anyone could have done that.",
            "They only said that to be nice.",
            "I only succeeded because I got lucky.",
        ],
        "reframe": "Positive experiences are real and count. Dismissing them maintains a negative self-view.",
        "challenge_prompt": "If a friend achieved what you did, would you disqualify their success?",
    },
    "overgeneralization": {
        "name": "Overgeneralization",
        "description": "Drawing broad conclusions from a single event.",
        "examples": [
            "I failed this one time, so I always fail.",
            "This always happens to me.",
            "No one ever listens to me.",
        ],
        "reframe": "One instance does not predict all future instances. Patterns need multiple data points.",
        "challenge_prompt": "Is this really always or never, or is this one specific situation?",
    },
    "personalization": {
        "name": "Personalization",
        "description": "Taking excessive responsibility for events outside your control.",
        "examples": [
            "They are upset, it must be my fault.",
            "If I had done more, this would not have happened.",
            "My mood affects everything around me.",
        ],
        "reframe": "Many factors contribute to outcomes. You are responsible for your choices, not for everything that happens.",
        "challenge_prompt": "What else might have contributed to this that was outside your control?",
    },
}


CBT_EXERCISES: dict[str, CBTExercise] = {
    "thought_record_full": CBTExercise(
        exercise_id="thought_record_full",
        name="CBT Thought Record",
        description=(
            "A structured 7-step thought record to identify, examine, and reframe "
            "distorted automatic thoughts. This is the foundational CBT skill."
        ),
        target_symptoms=[
            "anxiety",
            "depression",
            "rumination",
            "low_mood",
            "self_criticism",
        ],
        duration_minutes=20,
        steps=[
            "Step 1 - Situation. Describe the situation as factually as possible. "
            "Where were you? What was happening? Who was present? Write this in 1-2 sentences.",
            "Step 2 - Emotions. List every emotion you felt in that moment. "
            "Rate how intense each emotion was from 0 (not at all) to 100 (the most intense).",
            "Step 3 - Automatic Thought. What went through your mind in that moment? "
            "Write the exact thought or image, even if it feels irrational. "
            "Ask yourself: what am I telling myself about this situation?",
            "Step 4 - Cognitive Distortion. Which thinking patterns fit this thought? "
            "Look at: all-or-nothing, catastrophizing, mind reading, fortune telling, "
            "emotional reasoning, should statements, labeling, mental filter, disqualifying positives, "
            "overgeneralization, or personalization.",
            "Step 5 - Evidence For. What facts support this thought? "
            "List only objective facts, not feelings or interpretations.",
            "Step 6 - Evidence Against. What facts contradict this thought? "
            "What would a caring friend say about this? What would you say to someone else in this situation?",
            "Step 7 - Balanced Thought. Based on all the evidence, write a more balanced thought. "
            "This is not forced positivity but a realistic alternative. "
            "Then re-rate your emotions 0-100. Notice if the intensity has changed.",
        ],
        user_guidance=(
            "Guide through one step at a time. Wait for the user's answer before moving forward. "
            "If they struggle with Step 3, ask: 'What were you telling yourself about what was happening?' "
            "If Step 5 has no evidence, gently note that this often happens with distorted thoughts. "
            "At the end, reflect what they wrote and note any patterns across their distortions."
        ),
        follow_up_prompts=[
            "How did the emotional rating change after writing the balanced thought?",
            "Which distortion pattern do you notice most often in your thinking?",
            "Can you use this thought record again next time you notice strong emotions?",
        ],
    ),
    " downward_arrow": CBTExercise(
        exercise_id="downward_arrow",
        name="Downward Arrow Technique",
        description=(
            "A Socratic questioning technique that drills down from surface thoughts "
            "to core beliefs and underlying fears."
        ),
        target_symptoms=["anxiety", "avoidance", "fear", "procrastination"],
        duration_minutes=15,
        steps=[
            "Step 1 - Identify the Surface Thought. Start with the automatic thought "
            "that comes up in a difficult situation. Write it exactly as it appears.",
            "Step 2 - First Downward Arrow. Ask: 'If this thought were true, what would be so bad about that?' "
            "Continue asking this question after each answer, drilling deeper.",
            "Step 3 - Continue Drilling. Each answer leads to a deeper fear, belief, or meaning. "
            "Keep asking: 'And if that were true, what would be so bad about that?' "
            "Continue until you reach a core belief or fundamental fear.",
            "Step 4 - Identify the Core Belief. Common core beliefs include: "
            "'I am not good enough,' 'I am unlovable,' 'I am unsafe,' 'I am powerless.' "
            "Write down the core belief you reached.",
            "Step 5 - Examine the Core Belief. Is this belief absolutely true? "
            "What evidence contradicts it? Has there been a time when this belief was not accurate?",
            "Step 6 - Develop a New Belief. Write a more flexible, realistic belief "
            "that accounts for the evidence. Example: 'I sometimes make mistakes, like everyone does. "
            "That does not make me a failure.'",
        ],
        user_guidance=(
            "This exercise requires patience. Go slowly. "
            "If the user resists, note that it is normal to feel uncomfortable with deeper fears. "
            "Do not force them to go deeper than they can handle. "
            "End with reassurance that identifying a core belief is a step toward changing it."
        ),
        follow_up_prompts=[
            "Does this core belief show up in other areas of your life?",
            "How long have you held this belief?",
            "What would change if you believed something different?",
        ],
    ),
    "behavioral_activation": CBTExercise(
        exercise_id="behavioral_activation",
        name="Behavioral Activation",
        description=(
            "Counteracts depression and low motivation by rebuilding activity patterns "
            "through structured, value-aligned action. The key principle: "
            "action precedes motivation, not the other way around."
        ),
        target_symptoms=[
            "depression",
            "low_mood",
            "anhedonia",
            "isolation",
            "exhaustion",
        ],
        duration_minutes=20,
        steps=[
            "Step 1 - Map Your Avoidance. What activities have you been avoiding? "
            "List them honestly. Avoidance maintains depression by reducing positive reinforcement.",
            "Step 2 - Rate Your Energy. On a scale of 1-10, how much energy do you have right now? "
            "Be honest. This determines the starting level of activity.",
            "Step 3 - Identify Values. What matters to you in life? "
            "Consider: relationships, health, growth, creativity, contribution, rest. "
            "Pick one area to focus on this week.",
            "Step 4 - Design a Activation Task. Create a small, specific activity aligned with your value. "
            "It must be: achievable right now, slightly below your current energy level, "
            "concrete enough to do without decision fatigue.",
            "Step 5 - Schedule It. Write the exact time and place for this activity. "
            "Example: 'Tomorrow at 10am, I will walk to the park and sit for 10 minutes.'",
            "Step 6 - Anticipate Obstacles. What might get in the way? "
            "Plan for at least one obstacle. If the obstacle occurs, what will you do instead?",
            "Step 7 - Do It and Review. After completing the activity, note: "
            "What was the actual energy cost? Did your mood change? What did you notice?",
        ],
        user_guidance=(
            "The most important message: do not wait for motivation. "
            "Start with the smallest possible activity. Walking to the mailbox counts. "
            "Praise any step in the right direction, no matter how small. "
            "If they cannot complete an activity, explore what got in the way without judgment."
        ),
        follow_up_prompts=[
            "How did your mood change after the activity?",
            "What did you learn about the connection between action and mood?",
            "What is one slightly larger activity you could try this week?",
        ],
    ),
    "exposure_hierarchy": CBTExercise(
        exercise_id="exposure_hierarchy",
        name="Exposure Hierarchy Builder",
        description=(
            "Builds a graduated fear hierarchy for systematic desensitization. "
            "Used for phobias, social anxiety, OCD, and panic disorder."
        ),
        target_symptoms=["anxiety", "phobia", "panic", "avoidance", "OCD"],
        duration_minutes=25,
        steps=[
            "Step 1 - Identify the Fear. What is the specific thing, situation, or activity you avoid? "
            "Write it as concretely as possible.",
            "Step 2 - Rate SUDS. Rate how much anxiety this fear causes from 0 (no anxiety) to 100 (extreme anxiety). "
            "Use the Subjective Units of Distress Scale to calibrate.",
            "Step 3 - List Avoidance Behaviors. What do you currently do to avoid this fear? "
            "List each avoidance strategy.",
            "Step 4 - Build the Hierarchy. Create 8-10 situations ranked from easiest to hardest. "
            "Each level should be 5-15 SUDS points apart. "
            "Start with situations that cause 10-30 SUDS. Build up to 70-90 SUDS.",
            "Step 5 - Start at the Bottom. Begin with the easiest item. "
            "Practice it repeatedly until it causes less than 20 SUDS before moving up.",
            "Step 6 - Practice with Habituation. Repeat each level multiple times. "
            "Anxiety naturally decreases with repeated, prolonged exposure. "
            "Do not escape or avoid during the exposure. Stay until anxiety drops by half.",
            "Step 7 - Review and Adjust. After each session, note the peak anxiety and how long it took to decrease. "
            "Celebrate progress. Adjust the hierarchy as needed.",
        ],
        user_guidance=(
            "This is a gradual process. Rushing up the hierarchy causes more anxiety. "
            "Reinforce that some anxiety during exposure is expected and healthy. "
            "The goal is not zero anxiety but developing the ability to tolerate it. "
            "If a level is too hard, break it into smaller steps."
        ),
        follow_up_prompts=[
            "What is the smallest step you could practice today?",
            "What helps you stay in the exposure longer?",
            "How has your anxiety decreased since you started practicing?",
        ],
    ),
    "worst_best_realistic": CBTExercise(
        exercise_id="worst_best_realistic",
        name="Worst-Best-Realistic",
        description=(
            "A three-column exercise that challenges catastrophizing by examining the full range of possible outcomes."
        ),
        target_symptoms=[
            "anxiety",
            "worry",
            " catastrophizing",
            "uncertainty_intolerance",
        ],
        duration_minutes=15,
        steps=[
            "Step 1 - Identify the Worry. Write down the specific thing you are worried about.",
            "Step 2 - Worst Case Column. If the absolute worst case happened, what would occur? "
            "Be specific. Then: how likely is this? Rate 0-100%. "
            "And: if it happened, what would you do? How would you cope?",
            "Step 3 - Best Case Column. If the absolute best case happened, what would occur? "
            "Then: how likely is this? Rate 0-100%.",
            "Step 4 - Realistic Case Column. Based on evidence and past experience, "
            "what is the most likely actual outcome? "
            "What is 70-80% likely to happen? "
            "How does this compare to the worst and best cases?",
            "Step 5 - Reflect. Did completing this change how likely the worst case seems? "
            "Often the worst case is very unlikely, and the realistic case is much more manageable.",
        ],
        user_guidance=(
            "This is particularly helpful for decision anxiety and anticipatory worry. "
            "Encourage writing rather than just thinking through it. "
            "The coping plan in step 2 is critical - even worst cases usually have a coping response."
        ),
        follow_up_prompts=[
            "Does knowing the realistic outcome reduce the worry?",
            "What resources do you have if the realistic outcome occurs?",
            "Is there a decision you have been avoiding that this could help with?",
        ],
    ),
    "cost_benefit_analysis": CBTExercise(
        exercise_id="cost_benefit_analysis",
        name="Cost-Benefit Analysis",
        description=(
            "A structured decision-making tool that examines the pros and cons "
            "of a behavior, belief, or course of action."
        ),
        target_symptoms=[
            "ambivalence",
            "procrastination",
            "avoidance",
            "habitual_behavior",
        ],
        duration_minutes=15,
        steps=[
            "Step 1 - Define the Behavior. Clearly state the behavior, thought pattern, or decision you are weighing.",
            "Step 2 - Short-Term Costs. What are the immediate downsides of doing/keeping this?",
            "Step 3 - Short-Term Benefits. What are the immediate upsides of doing/keeping this?",
            "Step 4 - Long-Term Costs. If this continues for months or years, what are the consequences?",
            "Step 5 - Long-Term Benefits. If this continues long-term, what might be positive?",
            "Step 6 - Overall Balance. On balance, does this serve your wellbeing and values? "
            "What does this analysis suggest you should do?",
        ],
        user_guidance=(
            "This is especially useful for ambivalence - when part of you wants to change and part does not. "
            "Both sides usually have valid points. The goal is clarity, not forcing change. "
            "If the analysis is balanced, help them identify which side feels more important long-term."
        ),
        follow_up_prompts=[
            "What does the long-term picture suggest?",
            "What is one small step toward the more helpful choice?",
            "What gets in the way of following through on this?",
        ],
    ),
    "self_compassion_letter": CBTExercise(
        exercise_id="self_compassion_letter",
        name="Self-Compassion Letter",
        description=(
            "Writes a letter to oneself from the perspective of an unconditionally "
            "compassionate friend. Counteracts harsh self-criticism."
        ),
        target_symptoms=[
            "self_criticism",
            "shame",
            "low_self_esteem",
            "perfectionism",
            "depression",
        ],
        duration_minutes=20,
        steps=[
            "Step 1 - Identify the Trigger. What did you do, fail at, or feel ashamed of? "
            "Describe it factually without judgment.",
            "Step 2 - Notice the Inner Critic. What harsh words are you saying to yourself about this? "
            "Write them down. Then write: 'The comments I am making to myself are...'",
            "Step 3 - Shift Perspective. Now imagine a friend who loves you unconditionally. "
            "They know everything about this situation. What would they say to you?",
            "Step 4 - Write the Letter. From this friend's perspective, write a letter to yourself. "
            "Include: acknowledgment of the difficulty, reminder that imperfection is human, "
            "reassurance that you are doing your best, and encouragement.",
            "Step 5 - Read It Back. Read the letter aloud. "
            "Notice what it feels like to receive compassion from this friend.",
            "Step 6 - Connect to Values. How does this compassionate friend relate to your values? "
            "What would they want for your wellbeing?",
        ],
        user_guidance=(
            "Self-compassion is often harder than self-criticism. If this feels fake or uncomfortable, "
            "that is normal. Start with small acts of kindness rather than grand statements. "
            "Remind users that self-compassion is not self-pity or excuse-making. "
            "It is the recognition that struggle is part of the shared human experience."
        ),
        follow_up_prompts=[
            "How does it feel to receive compassion from this friend?",
            "What would it take to speak to yourself this kindly more often?",
            "Can you write this letter again next time the inner critic is loud?",
        ],
    ),
    "mindfulness_body_scan": CBTExercise(
        exercise_id="mindfulness_body_scan",
        name="Mindful Body Scan for Anxiety",
        description=(
            "A structured body scan that interrupts anxiety's physical loop by directing "
            "attention systematically through the body."
        ),
        target_symptoms=["anxiety", "panic", "stress", "hyperarousal", "racing_heart"],
        duration_minutes=10,
        steps=[
            "Step 1 - Settle In. Sit or lie in a comfortable position. Close your eyes if safe to do so. "
            "Take three slow, deep breaths. Breathe in through the nose, out through the mouth.",
            "Step 2 - Anchor to Feet. Bring attention to your feet. "
            "Notice any sensations: warmth, coolness, tingling, tension, nothing at all. "
            "Stay here for 30 seconds without trying to change anything.",
            "Step 3 - Move to Legs. Shift attention up to your lower legs, calves, knees. "
            "Notice what is present. If you find tension, see if you can soften it slightly on the exhale.",
            "Step 4 - Torso. Move to your stomach, lower back, chest. "
            "Notice the rise and fall of breathing. Does the chest feel tight or open?",
            "Step 5 - Hands and Arms. Notice your hands. Are they clenched or open? "
            "Move attention up through forearms, elbows, upper arms, shoulders.",
            "Step 6 - Head and Face. Notice your jaw, forehead, eyes. "
            "Are you holding tension here? Consciously release it on an exhale.",
            "Step 7 - Whole Body. Take a moment to feel the body as a whole. "
            "Notice how it feels right now, even if that is tired, tense, or restless. "
            "This is the present moment.",
        ],
        user_guidance=(
            "If the user experiences dissociation, guide them to press feet firmly into the floor. "
            "If they report physical sensations of panic, normalize this: "
            "'Anxiety makes the body feel intense sensations. These are not dangerous even though they feel alarming.' "
            "Offer this as a daily practice, not just during distress."
        ),
        follow_up_prompts=[
            "What areas of your body hold the most tension?",
            "How did your anxiety level change during the scan?",
            "Could you practice this for five minutes every morning?",
        ],
    ),
    "worry_tree": CBTExercise(
        exercise_id="worry_tree",
        name="Worry Tree Decision Tool",
        description=("分流担忧的工具性问题，区分可以解决和无法解决的担忧。"),
        target_symptoms=["anxiety", "worry", "rumination", " GAD"],
        duration_minutes=10,
        steps=[
            "Step 1 - Identify the Worry. Write down the specific worry exactly as it appears.",
            "Step 2 - Is there something you can do about it right now? "
            "If yes: proceed to Step 3. If no: proceed to Step 4.",
            "Step 3 - If Actionable. Write down the specific action you can take. "
            "What is one concrete step you can take in the next hour? "
            "Schedule when you will do it. Then postpone the worry until that scheduled time.",
            "Step 4 - If Not Actionable. Ask: Is this something you can control? "
            "If no: Accept that this is outside your control. "
            "Try saying: 'This is out of my hands. I choose to let it go for now.' "
            "If yes but not right now: Schedule a 10-minute worry time later. "
            "Write the worry and the time on a notepad.",
            "Step 5 - Postpone the Worry. If the worry recurs during the day, "
            "note it briefly and tell yourself: 'I will address this at the scheduled time.' "
            "This trains the mind to defer rather than ruminate continuously.",
        ],
        user_guidance=(
            "This is particularly effective for Generalized Anxiety Disorder. "
            "The key principle is scheduling worry rather than suppressing it. "
            "Worry postponed to a specific time is less likely to dominate the whole day."
        ),
        follow_up_prompts=[
            "Which of your current worries is actually actionable right now?",
            "Could you schedule a 10-minute worry time each evening?",
            "What helps you accept the worries you cannot control?",
        ],
    ),
}


CBT_INTERVENTION_GUIDES: dict[str, str] = {
    "anxiety_general": (
        "Generalized Anxiety Intervention Guide: Anxiety is the brain's alarm system activating "
        "in response to perceived threat. The goal is not to eliminate anxiety but to reduce its frequency, "
        "intensity, and interference. Key principles: (1) Anxiety is a normal emotion with a useful purpose. "
        "(2) Avoidance maintains and grows anxiety. (3) Facing feared situations reduces anxiety over time. "
        "(4) Uncertain situations are often misinterpreted as dangerous. "
        "Recommended approach: Validate the felt sense of threat. Normalize that anxiety is uncomfortable "
        "but not dangerous. Explore what the anxiety might be trying to protect. "
        "Guide toward one small step of valued action despite the anxiety. "
        "Avoid reassurance-seeking loops. Set limits on safety behaviors gently but firmly."
    ),
    "depression_general": (
        "Depression Intervention Guide: Depression narrows cognition, drains energy, and distorts "
        "self-perception in negative directions. The most important principle: behavioral activation "
        "precedes mood change. Waiting for motivation to return is circular. "
        "Key interventions: (1) Build structure and routine immediately. "
        "(2) Introduce one small positive activity per day, even at very low energy. "
        "(3) Challenge hopeless thoughts using thought records. "
        "(4) Reduce withdrawal gradually through scheduling social contact. "
        "(5) Address sleep-wake cycles. "
        "Do not confuse depression with laziness or weakness. It affects thinking patterns deeply. "
        "If suicidal ideation emerges, follow crisis protocol immediately. "
        "Monitor for vegetative symptoms worsening. Consider whether professional evaluation is needed."
    ),
    "sleep_hygiene": (
        "Sleep Intervention Guide: Insomnia maintains itself through conditioning. "
        "The bedroom becomes associated with wakefulness rather than sleep. "
        "Key principles: (1) Sleep is homeostatic and circadian. Both systems must align. "
        "(2) Stimulus control: the bed should only be for sleep and intimacy. "
        "If awake for more than 20 minutes, leave the bed and return when sleepy. "
        "(3) Sleep restriction: limit time in bed to actual sleep time to build sleep pressure. "
        "(4) Caffeine cutoff at least 6 hours before bed. "
        "(5) Screen time reduction 60 minutes before bed. "
        "(6) Consistent wake time every day, including weekends. "
        "Avoid napping if it reduces sleep pressure. "
        "Address racing thoughts with a pre-sleep worry journal: write thoughts and set them aside."
    ),
    "panic_attacks": (
        "Panic Attack Intervention Guide: A panic attack is a false alarm of the body. "
        "The physical sensations of panic (racing heart, shortness of breath, dizziness) are not dangerous. "
        "They are the body's evolutionary alarm system activating inappropriately. "
        "Key interventions during a panic attack: "
        "(1) Ground immediately using 5-4-3-2-1 senses. "
        "(2) Slow down breathing deliberately. The body is already getting enough oxygen. "
        "Hyperventilation prolongs panic. "
        "(3) Label what is happening: 'This is a panic attack. It will pass. I am safe.' "
        "(4) Do not flee unless necessary for safety. Staying reduces the fear of panic. "
        "Between attacks: explore what panic attacks are triggered by. "
        "Address any catastrophic misinterpretations of bodily sensations (anxiety sensitivity). "
        "If panic attacks are frequent or unpredictable, recommend professional assessment."
    ),
    "rumination": (
        "Rumination Intervention Guide: Rumination is repetitive, recursive thinking about "
        "negative content. It serves no problem-solving function and maintains depression and anxiety. "
        "Key principles: (1) Rumination feels like problem-solving but it is not. "
        "(2) The urge to ruminate is an emotional habit. "
        "(3) Interruption and redirection are more effective than suppression. "
        "Techniques: cognitive defusion, behavioral interruption, attention shifting, "
        "worry postponement, engaging in opposite behavior. "
        "The most effective behavioral interruption: engage the body physically. "
        "Walking, cold water on face, changing environment, or singing disrupts the rumination loop. "
        "If rumination centers on past trauma or loss, this may require deeper therapeutic work."
    ),
    "anger": (
        "Anger Intervention Guide: Anger is a protective emotion activated when a boundary is crossed "
        "or a need is not met. It is not inherently destructive. "
        "Key principles: (1) Anger is usually secondary to a more vulnerable emotion (fear, hurt, shame). "
        "Identifying the primary emotion reduces anger intensity. "
        "(2) Anger increases with physical arousal. Slowing the body slows the anger. "
        "(3) Anger makes thinking more rigid. Decisions made in anger are usually regretted. "
        "Interventions: (1) Time out: create physical distance before responding. "
        "(2) Physiological cooling: cold water, slow breathing, muscle relaxation. "
        "(3) Reframe: what is the unmet need beneath the anger? "
        "(4) Assertive communication: express needs clearly without aggression. "
        "Avoid: expressing anger at people while highly aroused, making threats, "
        "or using violence. These are never acceptable regardless of provocation."
    ),
    "procrastination": (
        "Procrastination Intervention Guide: Procrastination is not laziness or poor time management. "
        "It is primarily an emotion regulation problem. People procrastinate to avoid "
        "aversive feelings associated with the task: anxiety, fear of failure, perfectionism, overwhelm. "
        "Key interventions: (1) Reduce task aversiveness. Make the task smaller, shorter, less demanding. "
        "'Just five minutes' is more effective than 'finish this project.' "
        "(2) Reduce performance pressure. Lower the standard for the first attempt. "
        "'Done and imperfect' beats 'perfect and never started.' "
        "(3) Address the avoidance payoff. What feeling am I escaping? Can I tolerate it for 5 minutes? "
        "(4) Build implementation intentions: 'When X happens, I will do Y.' "
        "Reduce reliance on motivation which is unreliable. Build habits instead."
    ),
}


def get_cbt_distortions() -> dict:
    return COGNITIVE_DISTORTIONS


def get_cbt_exercise(exercise_id: str) -> CBTExercise | None:
    return CBT_EXERCISES.get(exercise_id)


def get_cbt_guide(topic: str) -> str | None:
    return CBT_INTERVENTION_GUIDES.get(topic)


def list_cbt_exercises() -> dict[str, str]:
    return {k: v.name for k, v in CBT_EXERCISES.items()}
