"""Comprehensive ACT (Acceptance and Commitment Therapy) knowledge base."""

ACT_CORE_PROCESSES = {
    "acceptance": {
        "description": (
            "Making room for painful feelings, sensations, and urges without trying to control, "
            "avoid, or eliminate them. This does not mean approving of suffering. It means "
            "stopping the struggle that adds a second layer of pain."
        ),
        "key_principle": "Struggling against pain often increases pain.",
        "typical_phrases": [
            "I wish I did not feel this way.",
            "I need to get rid of this anxiety before I can move forward.",
            "If I stop worrying, something bad will happen.",
        ],
        "intervention": "Help the user notice the struggle and explore the cost of avoidance. "
        "Introduce willingness as an alternative to control.",
        "metaphor": "Trying to push a beach ball underwater takes enormous effort and never works. "
        "Instead, you can make room for it and keep walking.",
    },
    "cognitive_defusion": {
        "description": (
            "Learning to see thoughts as thoughts, not as facts or threats. "
            "Defusion techniques create psychological distance from the content of thoughts."
        ),
        "key_principle": "You are not your thoughts. Thoughts are just words.",
        "typical_phrases": [
            "My thought says I am worthless, so I must be.",
            "I believe I will fail so I should not even try.",
            "The thought keeps repeating that I am in danger.",
        ],
        "intervention": "Use defusion techniques to separate the person from the thought. "
        "Thoughts are mental events, not definitions of reality.",
        "metaphor": "Thoughts are like clouds passing through the sky of awareness. "
        "You are the sky, not any single cloud.",
    },
    "present_moment": {
        "description": (
            "Contacting the present moment with full awareness. Much human suffering comes from "
            "living in the past (rumination, regret) or future (worry, anticipation). "
            "The only moment available for action is now."
        ),
        "key_principle": "The present moment is all there ever is.",
        "typical_phrases": [
            "I keep replaying what went wrong.",
            "What if this happens again?",
            "I cannot enjoy anything because I am worried about the future.",
        ],
        "intervention": "Guide attention back to direct sensory experience of the present moment. "
        "Notice what is happening right now, externally and internally, without judgment.",
        "metaphor": "Life happens in the present moment, not in the past you keep revisiting "
        "or the future you keep anticipating.",
    },
    "self_as_context": {
        "description": (
            "Distinguishing between the conceptualized self (all the labels, stories, "
            "and judgments we hold about ourselves) and the observing self (the consistent "
            "perspective from which all experience is noticed). "
            "The observing self is always present, always capable, and cannot be destroyed."
        ),
        "key_principle": "You are the context, not the content.",
        "typical_phrases": [
            "I am a failure.",
            "I am worthless.",
            "I have always been this way and always will be.",
        ],
        "intervention": "Help the user notice the perspective from which they observe their thoughts, "
        "feelings, and experiences. This perspective is constant. The content changes constantly.",
        "metaphor": "The stage on which all thoughts, feelings, and experiences perform. "
        "The performance changes night after night. The stage remains.",
    },
    "values": {
        "description": (
            "Chosen life directions the user wants to move toward. Values are not goals (which are achievable) "
            "but directions that can always be moving toward. They give meaning and direction to action."
        ),
        "key_principle": "Values are chosen directions, not achieved destinations.",
        "typical_values": {
            "relationships": "Being a caring partner, friend, family member.",
            "health": "Taking care of physical and mental wellbeing.",
            "growth": "Continual learning and personal development.",
            "contribution": "Giving to others and the community.",
            "creativity": "Expressing oneself through creative outlets.",
            "leisure": "Enjoying life through hobbies and rest.",
        },
        "intervention": "Help the user identify what truly matters, separate from what others expect. "
        "Values are chosen, not inherited. They should feel vital and alive, not obligatory.",
        "metaphor": "Values are like a compass pointing north. You will never arrive at north, "
        "but every step in the right direction is meaningful.",
    },
    "committed_action": {
        "description": (
            "Taking effective action guided by values, even in the presence of difficult thoughts "
            "and feelings. Action is not dependent on feeling ready or confident."
        ),
        "key_principle": "Action creates motivation, not the other way around.",
        "typical_phrases": [
            "I will do it when I feel confident.",
            "I need to resolve all my emotions first.",
            "I am not ready yet.",
        ],
        "intervention": "Help the user identify one small value-aligned action they can take today, "
        "regardless of how they feel. Progress in valued directions builds momentum.",
        "metaphor": "A sailor does not wait for calm seas to set sail. "
        "They sail in the direction that matters, regardless of the weather.",
    },
}


ACT_EXERCISES = {
    "defusion_tunnel": {
        "name": "Defusion: The Thought Tunnel",
        "description": (
            "Visualize the difficult thought as a train passing through a tunnel. "
            "You are standing outside the tunnel watching the train go by. "
            "The train (thought) may be loud, frightening, or convincing. "
            "But it passes through and leaves. You remain."
        ),
        "steps": [
            "Identify the difficult thought.",
            "Visualize standing outside a long tunnel. Imagine the thought as a train entering the tunnel.",
            "Watch it enter. Hear its sound. Notice its weight and speed.",
            "Watch it travel through the tunnel. Notice it is just passing through.",
            "Watch it exit on the other side and disappear into the distance.",
            "The tunnel is always here. Thoughts come and go. You are watching them.",
        ],
        "tips": "Remind the user: the train (thought) is loud but it leaves. You do not have to jump on it.",
    },
    "defusion_sing": {
        "name": "Defusion: Sing the Thought",
        "description": (
            "Sing the difficult thought aloud to the tune of a familiar song. "
            "This sounds silly and that is exactly the point. "
            "Singing separates the content of the thought from its emotional charge."
        ),
        "steps": [
            "Identify the difficult thought.",
            "Choose a familiar tune (e.g., Happy Birthday, a children's song).",
            "Sing the thought to that tune.",
            "Notice how the meaning of the words changes when sung.",
            "Notice how the emotional weight changes.",
        ],
        "tips": "This works because the brain processes singing in a different area than threatening content. "
        "It is difficult to feel as threatened by a thought sung in a silly tune.",
    },
    "defusion_labeling": {
        "name": "Defusion: Adding the Label",
        "description": (
            "Add the prefix 'I am having the thought that...' before the difficult thought. "
            "This simple linguistic shift creates distance."
        ),
        "steps": [
            "Identify the difficult thought.",
            "Say it with the prefix: 'I am having the thought that...'",
            "Then try: 'I notice I am having the thought that...'",
            "Then try: 'There is a thinking going on that...'",
            "Notice how each version creates slightly more distance.",
        ],
        "tips": "If the thought is 'I am a failure,' try: "
        "'I am noticing my mind is telling me I am a failure.' The distance grows.",
    },
    "acceptance_leaves": {
        "name": "Acceptance: Leaves on a Stream",
        "description": (
            "A visualization practice for making room for difficult feelings. "
            "You are sitting beside a stream. Difficult feelings and thoughts float by on leaves. "
            "You do not grab them or push them away. You let them float past."
        ),
        "steps": [
            "Find a comfortable seated position. Close your eyes if safe.",
            "Visualize a gentle stream flowing beside you. The water is calm.",
            "Notice each thought or feeling that arises. Place it on a leaf floating on the stream.",
            "Watch the leaf carry the thought down the stream and out of view.",
            "If the same thought returns, place it on another leaf.",
            "You are the bank of the stream. The leaves come and go. You remain.",
        ],
        "tips": "The goal is not to empty the stream. The goal is to stop fighting the current.",
    },
    "values_card_sort": {
        "name": "Values Card Sort",
        "description": (
            "A structured values clarification exercise. "
            "Identify the 3-5 life areas that matter most, then narrow to the single most important one. "
            "Then identify one small action in that direction."
        ),
        "steps": [
            "Consider these life areas: Family, Friendships, Intimate Relationships, Career, "
            "Education, Health, Physical Activity, Creativity, Leisure, Personal Growth, Community, Spirituality.",
            "Which 3-5 feel most important to you right now?",
            "If you could only focus on one this month, which would it be?",
            "Describe what a day fully aligned with this value would look like.",
            "Rate how close your current life is to that day, 0-10.",
            "Name one small action this week that moves toward that value.",
        ],
        "tips": "Values are not about perfection. They are about direction. Any step in a valued direction counts.",
    },
    "commitment_obstacle": {
        "name": "Committed Action: Obstacle Course",
        "description": (
            "Identify obstacles (internal and external) to taking a valued action. "
            "Then identify ways to act despite those obstacles."
        ),
        "steps": [
            "State the valued action you want to take.",
            "Identify the internal obstacles: difficult feelings, thoughts, urges, physical sensations.",
            "Identify the external obstacles: practical barriers, other people's behavior, resources.",
            "For each obstacle: Is this something I can do something about? If yes, plan for it. "
            "If no, make room for it and keep moving.",
            "Take the first step today, regardless of the obstacles.",
        ],
        "tips": "Obstacles are normal. Acting despite obstacles is what commitment means.",
    },
    "observing_self": {
        "name": "Self as Context: The Lighthouse",
        "description": (
            "Visualization of the observing self as a lighthouse. "
            "Thoughts, feelings, and events are ships passing in the night. "
            "The lighthouse does not follow the ships or change for them. "
            "It remains steady, shining light regardless."
        ),
        "steps": [
            "Visualize a lighthouse on a rocky coastline. The sea is active with waves.",
            "Ships of different sizes pass in the darkness. Each ship represents a thought or feeling.",
            "Some ships are frightening. Some are large. Some are small.",
            "The lighthouse notices all of them but remains exactly where it is.",
            "You are the lighthouse. The ships come and go. Your job is to shine the light.",
        ],
        "tips": "This is particularly useful for users who feel overwhelmed by the volume or intensity of thoughts.",
    },
    "willingness_choice": {
        "name": "Willingness vs Control",
        "description": (
            "A structured exercise to examine the cost of control strategies "
            "and explore the possibility of willingness."
        ),
        "steps": [
            "List all the things you try to control in your life right now.",
            "For each: How exhausting is this effort? Rate 0-10.",
            "What does control cost you in terms of energy, time, relationships, opportunity?",
            "If you stopped trying to control this one thing, what might you do with that energy?",
            "Are you willing to try letting go of just this one thing, just for today?",
        ],
        "tips": "Willingness is not resignation. It is choosing to stop fighting an unwinnable battle "
        "so energy can go toward what matters.",
    },
}


def get_act_processes() -> dict:
    return ACT_CORE_PROCESSES


def get_act_exercise(exercise_id: str) -> dict | None:
    return ACT_EXERCISES.get(exercise_id)


def list_act_exercises() -> dict[str, str]:
    return {k: v["name"] for k, v in ACT_EXERCISES.items()}
