#!/usr/bin/env python3

'''
Tests `gitp clone` functionality.
'''

import os, sys, shutil, glob
import pytest
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import utils
from gitparent import cli

@utils.set_workarea()
def test_basic(p):
    ''' Basic clone of remote and child repo creation. '''
    top = utils.init_simple_remote('top')
    child = utils.init_simple_remote('child')
    gchild = utils.init_simple_remote('gchild')
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

@utils.set_workarea()
def test_local_clone(p):
    ''' Clone copy of remote. ''' 
    test_basic()
    cli.clone([p, p + '.copy'])
    os.chdir(p + '.copy')
    cli.status([])

@utils.set_workarea()
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