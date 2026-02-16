from core.llm_manager import BrainRouter

# Test both models
router = BrainRouter()

print("\n" + "="*50)
print("Testing GROQ (Fast Brain)")
print("="*50)
groq_brain = router.get_brain(task_type="fast")
if groq_brain:
    try:
        response = groq_brain.invoke("Say 'Groq Online'")
        print(f"✅ Groq Response: {response.content}")
    except Exception as e:
        print(f"❌ Groq Error: {e}")

print("\n" + "="*50)
print("Testing GEMINI (Smart Brain)")
print("="*50)
gemini_brain = router.get_brain(task_type="vision")
if gemini_brain:
    try:
        response = gemini_brain.invoke("Say 'Gemini Online'")
        print(f"✅ Gemini Response: {response.content}")
    except Exception as e:
        print(f"❌ Gemini Error: {e}")
