import tokenize

with open("app/services/action_plan_generator.py", "rb") as f:
    try:
        for token in tokenize.tokenize(f.readline):
            pass
        print("Tokenization successful!")
    except tokenize.TokenError as e:
        print(f"TokenError: {e}")
    except IndentationError as e:
        print(f"IndentationError: {e}")
    except SyntaxError as e:
        print(f"SyntaxError: {e}")
