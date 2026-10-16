"""Create local credentials once. This is not a Python package installer."""
import os
import secrets

try:
    fd = os.open('.env', os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
except FileExistsError:
    print('.env already exists; left unchanged.')
else:
    with os.fdopen(fd, 'w') as f:
        f.write('API_KEY=' + secrets.token_urlsafe(32) + '\nBIND_HOST=127.0.0.1\n')
    print('Created .env. Use API_KEY from this file in the GUI and HTTP requests.')
