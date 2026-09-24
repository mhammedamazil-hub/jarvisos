# AI provider examples (keys are prompted with hidden input and stored 0600 / keyring — never logged)
jarvis provider add gemini --key                                   # template: gemini-2.5-flash (text+vision)
jarvis provider add mistral --key                                  # mistral-small / codestral / pixtral
jarvis provider add openrouter --key --model text=anthropic/claude-3.5-haiku
jarvis provider add huggingface --key
jarvis provider add mylab --base-url https://llm.example.com/v1 --model text=my-model --model coding=my-coder --key
jarvis provider add ollama --local-model --model text=qwen2.5:0.5b  # optional small local model (offline)
# routing: "Use Gemini for vision and Mistral for coding"
jarvis provider route gemini --model vision
jarvis provider route mistral --model coding
jarvis provider test gemini
