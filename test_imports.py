import os, sys, traceback

out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'test_out.txt')
with open(out_path, 'w') as f:
    f.write('Script started\n')
    f.flush()
    
    try:
        from app.core.config import settings
        f.write(f'Settings OK: {settings.PROJECT_NAME}, env={settings.ENVIRONMENT}\n')
    except Exception as e:
        f.write(f'Config error: {e}\n')
        traceback.print_exc(file=f)
    f.flush()
    
    try:
        from app.core.database import engine, _use_sqlite
        f.write(f'DB OK: sqlite={_use_sqlite}, engine={engine.url}\n')
    except Exception as e:
        f.write(f'DB error: {e}\n')
        traceback.print_exc(file=f)
    f.flush()
    
    try:
        from app.main import app
        f.write(f'App OK: {app.title}\n')
    except Exception as e:
        f.write(f'App error: {e}\n')
        traceback.print_exc(file=f)
    f.flush()
    
    f.write('DONE\n')
