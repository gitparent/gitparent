#!/usr/bin/env python3

'''
Tests `gitp clone` functionality.
'''

import os, sys, shutil, glob
import pytest
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import utils
from gitparent import cli


def cleanup(f):
    def wrap(*args, **kwargs):
        cwd = os.getcwd()
        if os.path.exists('.dut_remotes'):
            shutil.rmtree('.dut_remotes')
        if os.path.exists('.dut_local'):
            for x in glob.glob('.dut_local*'):
                shutil.rmtree(x)
        ans = f('.dut_local', *args, **kwargs)
        os.chdir(cwd)
        return ans
    return wrap

def init_remote(path):
    utils.create_remote(f'.dut_remotes/{path}')
    utils.create_file(f'.dut_remotes/{path}/file1')
    utils.create_file(f'.dut_remotes/{path}/file2')
    utils.create_file(f'.dut_remotes/{path}/file3')
    utils.create_file(f'.dut_remotes/{path}/subdir/file_a')
    utils.create_file(f'.dut_remotes/{path}/subdir/file_b')
    return os.path.abspath(f'.dut_remotes/{path}')

@cleanup
def test_basic(p):
    ''' Basic clone of remote and child repo creation. '''
    top = init_remote('top')
    child = init_remote('child')
    gchild = init_remote('gchild')
    cli.clone([top, p])
    os.chdir(p)
    with pytest.raises(Exception): #no relative paths allowed
        cli.new(['--from', os.path.relpath(child, os.getcwd()), 'sub'])
    cli.new(['--from', child, 'sub'])
    cli.new(['--from', gchild, 'sub/subsub'])
    os.chdir('sub')
    cli.new(['--from', gchild, 'subsub2'])
    os.chdir('..')
    cli.add(['-A'])
    cli.commit(['-m', 'hello world'])
    cli.status([])

@cleanup
def test_local_clone(p):
    ''' Clone copy of remote. ''' 
    test_basic()
    cli.clone([p, p + '.copy'])
    os.chdir(p + '.copy')
    cli.status([])

@cleanup
def test_partial_local_clone(p):
    ''' Clone copy of remote that is partially missing. '''
    test_basic()
    shutil.rmtree(os.path.join(p, 'sub', 'subsub')) #remove part of the clone src
    cli.clone([p, p + '.copy'])
    os.chdir(p + '.copy')
    cli.status([])

if __name__ == "__main__":
    import pytest
    import sys
    if len(sys.argv) > 1:
        rc = pytest.main(args=['-s', '-vv', '-k', sys.argv[1], sys.argv[0]])
    else:
        rc = pytest.main(args=['-vv', sys.argv[0]])
    if rc:
        sys.exit(1)