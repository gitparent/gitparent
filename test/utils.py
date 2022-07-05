import os, subprocess
from subprocess import STDOUT
from gitparent import cli

def create_remote(path):
    os.makedirs(path)
    subprocess.check_output(['git', 'init'], cwd=path, stderr=STDOUT).decode('utf-8')

def create_file(path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w') as f:
        f.write('foobar')
    try:
        subprocess.check_output(['git', 'add', os.path.basename(path)], cwd=os.path.dirname(path), stderr=STDOUT).decode('utf-8')
        subprocess.check_output(['git', 'commit', '-m', f'Added {path}'], cwd=os.path.dirname(path), stderr=STDOUT).decode('utf-8')
    except subprocess.CalledProcessError as e:
        raise Exception(e.output.decode('utf-8'))
