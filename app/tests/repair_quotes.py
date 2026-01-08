import re

file_path = "app/services/action_plan_generator.py"

with open(file_path, "r") as f:
    content = f.read()

# Pattern: word starting with s, ending with '
# We want to replace it with 'word'
# \b(s\w*)'  -> '\1'
# But we must be careful not to match things that are already quoted?
# e.g. "it's symptoms'"? (unlikely)
# If we have `symptoms'`, it likely has a space or delimiter before it.

# We will use re.sub
# But wait, what if `symptoms'` is inside a string?
# e.g. "List of symptoms'"
# If I change it to "List of 'symptoms'" it might be okay.
# But if it was "symptoms'", it's weird.

# The damage: 'symptoms' -> symptoms'
# So we expect [delimiter]symptoms'[delimiter]

def replace_method(match):
    word = match.group(1)
    return f"'{word}'"

# Regex:
# Look for 's' followed by word characters, ending in '
# Ensure it is NOT preceded by a quote (which would mean it's weirdly nested or already handled?)
# Actually, if it was 'symptoms', the opening quote is GONE.
# So it is preceded by [space, brackets, commas].
new_content = re.sub(r"\b(s\w+)'", replace_method, content)

# Special check for 'system's' -> 'systems' (already done by sed)
# We are fixing ONLY the case where opening quote was lost.

with open(file_path, "w") as f:
    f.write(new_content)

print("Repaired file.")
