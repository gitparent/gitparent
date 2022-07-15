import os, subprocess, shutil, glob
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

def set_workarea(tgt='.'):
    '''
    Decorator to initialize test collateral under the given `tgt` directory.
    '''
    def wrap(f):
        def _wrap(*args, **kwargs):
            cwd = os.getcwd()
            os.chdir(tgt)
            if os.path.exists('.dut_remotes'):
                shutil.rmtree('.dut_remotes')
            if os.path.exists('.dut_local'):
                for x in glob.glob('.dut_local*'):
                    shutil.rmtree(x)
            ans = f('.dut_local', *args, **kwargs)
            os.chdir(cwd)
            return ans
        return _wrap
    return wrap

def init_simple_remote(path):
    create_remote(f'.dut_remotes/{path}')
    create_file(f'.dut_remotes/{path}/file1')
    create_file(f'.dut_remotes/{path}/file2')
    create_file(f'.dut_remotes/{path}/file3')
    create_file(f'.dut_remotes/{path}/subdir/file_a')
    create_file(f'.dut_remotes/{path}/subdir/file_b')
    return os.path.abspath(f'.dut_remotes/{path}')