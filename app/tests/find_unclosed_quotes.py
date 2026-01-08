
file_path = "app/services/action_plan_generator.py"

with open(file_path, "r") as f:
    lines = f.readlines()

in_quote = False
quote_start = -1

for i, line in enumerate(lines):
    # This is a naive check, but might work for """
    # We count occurrences of """
    count = line.count('"""')
    
    # Check if there's a comment before the quotes?
    # Strip comments?
    code = line.split('#')[0]
    count = code.count('"""')
    
    if count % 2 != 0:
        # Toggles state
        if in_quote:
            print(f"Propable CLOSE at line {i+1}: {line.strip()}")
            in_quote = False
        else:
            print(f"Probable OPEN at line {i+1}: {line.strip()}")
            in_quote = True
            quote_start = i+1

if in_quote:
    print(f"ERROR: File ended inside quote! Started at line {quote_start}")
