#!/usr/bin/env python3

'''
Tests `gitp link` functionality.
'''

import os, sys, shutil, glob
import pytest
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import utils
from gitparent import cli

def init_repo(p):
    top = utils.init_simple_remote('top')
    child1 = utils.init_simple_remote('child1')
    gchild1 = utils.init_simple_remote('gchild1')
    child2 = utils.init_simple_remote('child2')
    gchild2 = utils.init_simple_remote('gchild2')
    cli.clone([top, p])
    os.chdir(p)
    cli.new(['--from', child1, 'child1'])
    os.chdir('child1')
    cli.new(['--from', gchild1, 'gchild1'])
    os.chdir('..')
    cli.new(['--from', child2, 'child2'])
    os.chdir('child2')
    cli.new(['--from', gchild2, 'gchild2'])
    os.chdir('..')
    return top, child1, child2, gchild1, gchild2

@utils.set_workarea()
def test_overlay_basic(p):
    ''' Basic creation of an overlay link. '''
    top, child1, child2, gchild1, gchild2 = init_repo(p)
    cli.status([])
    cli.exec(['ls'])
    cli.link(['--overlay', 'child1/gchild1', 'child2/gchild2', '-v', '3'])
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