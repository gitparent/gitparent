#!/usr/bin/env python3

'''
# Git Parent gitp CLI Utility

A git wrapper script to help manage multi-repo projects. 
See https://github.com/gitparent/gitparent for more information.
'''

#ANCHOR: Native Dependencies
import os, sys, fcntl, time, argparse, subprocess, shutil, enum, re, glob, shlex, datetime, socket, threading, functools, typing
from subprocess import Popen, PIPE, STDOUT
from filelock import FileLock


#ANCHOR: External Dependencies
import yaml


#ANCHOR: Globals
try:
    import pkg_resources
    VERSION = pkg_resources.require('gitparent')[0].version
except:
    VERSION = 'unknown'
DEFAULT_DEBUG_LEVEL = 0
DEBUG_LEVEL = DEFAULT_DEBUG_LEVEL
FORCE_COLORS = False
GIT_FALLBACK = False
CLI_RETURN_CODE = 0
PARSERS = {}


#ANCHOR: Utility Types
class RepoState(enum.Enum):
    ''' Enumeration used to describe the state of a child repo. '''
    
    CLEAN = 0
    ''' No local changes, aligned with `.gitp_manifest` '''
    MODIFIED = 2
    ''' Local changes present '''
    UNALIGNED = 3
    ''' No local changes, but not aligned with `.gitp_manifest` '''
    NONEXISTENT = 4
    ''' Found in `.gitp_manifest` but does not exist as a directory '''
    UNLINKED = 5
    ''' Defined as a link in `.gitp_manifest` but is not linked '''
    OVERLAYED = 6
    ''' Overridden via an overlay '''

class Style(enum.Enum):
    ''' Enumeration used for text styles. '''

    BOLD    = enum.auto()
    ITALIC  = enum.auto()
    BLUE    = enum.auto()
    CYAN    = enum.auto()
    GREEN   = enum.auto()
    RED     = enum.auto()
    YELLOW  = enum.auto()
    GRAY    = enum.auto()
    BLACK   = enum.auto()

class Manifest:
    ''' Internal representation of a `.gitp_manifest` file. '''

    class Repo:
        ''' Internal representation of a repo entry in a `.gitp_manifest` file. ''' 
        def __init__(self):
            self.url = self.branch = self.commit = self.link = self.link_newest = self.link_filter = self.type = None

        def __eq__(self, other):
            if not isinstance(other, Manifest.Repo):
                raise Exception(f"Equivalence operation between {type(self)} and {type(other)} is unsupported")
            return True if self.url == other.url and (self.branch == other.branch or (self.commit and (self.commit == other.commit))) and self.link == other.link and self.type == other.type else False

    def __init__(self, name:str, path:str=None, raw:typing.Union[dict,str]=None):
        '''
        Args:
            name: name of the associated gitp repo
            path: path to the gitp repo
            raw: either a string containing the contents of `.gitp_manifest` or a dict representation of a `.gitp_manifest` file's contents
        '''
        self.name = name
        ''' Repo name '''
        self.path = path
        ''' Path to the root of the gitp repo '''
        self.repos = {}
        ''' Child repo dictionary '''
        self.lock_server = None
        ''' Lock server address associated with this repo, if any '''
        self.post_clone = []
        ''' Commands to execute automatically after cloning this repo '''
        self.post_pull = []
        ''' Commands to execute automatically after pulling this repo '''
        if raw:
            self.populate(raw)
        elif path:
            self.read(path)

    def __setitem__(self, key:str, item:'Manifest.Repo'):
        self.repos[key] = item

    def __getitem__(self, key:str) -> 'Manifest.Repo':
        return self.repos[key]
    
    def __boolean__(self):
        return bool(self.repos)

    def __iter__(self):
        return self.repos.__iter__()

    def pop(self, key:str) -> 'Manifest.Repo':
        return self.repos.pop(key)

    def items(self):
        return self.repos.items()

    def get_overlays(self) -> typing.Dict[str, 'Manifest.Repo']:
        '''
        Returns all of the overlay entries associated with this repo.
        
        Returns:
            A dict mapping absolute paths of overlay targets to their `Repo` objects.
        '''
        overlay_entries = {}
        for child,child_info in self.items():
            if child_info.type == 'overlay':
                overlay_entries[os.path.join(os.path.dirname(self.path), child)] = child_info
        return overlay_entries

    def as_dict(self) -> dict:
        ''' Convertor method to dict representation. '''
        ans = {'repos': {}}
        for child,child_info in self.repos.items():
            ans['repos'][child] = {x:y for x,y in child_info.__dict__.items() if y is not None}
        return ans

    def write(self):
        ''' Writes the manifest to file. '''
        debug(f"Updating {os.path.relpath(self.path, os.getcwd())}")
        with open(self.path, 'w') as f:
            f.write(yaml.dump(self.as_dict()))

    def read(self, path:str):
        '''
        Read manifest from file.

        Args:
            str: path to the `.gitp_manifest` file to read
        '''
        with open(self.path, 'r') as f:
            ans = f.read()
        ans = yaml.safe_load(ans) or {}
        self.populate(ans)
        self.path = path

    def populate(self, raw:typing.Union[dict,str]):
        '''
        Parses a dict and populates attributes accordingly.

        Args:
            raw: either the content of a `.gitp_manifest` file representated as a string or a dict
        '''
        if isinstance(raw, str):
            raw = yaml.safe_load(raw)
        if 'lock_server' in raw and isinstance(raw['lock_server'], str):
            self.lock_server = raw['lock_server']
            m = re.search(r'^([^:]+):(\d+)$', self.lock_server.strip())
            if not m:
                raise Exception(f"Malformed server string '{self.lock_server}' -- must be of form <hostname>:<port>")
            self.lock_server = m.groups(0)
            self.lock_server = (self.lock_server[0], int(self.lock_server[1]))
        if 'post_clone' in raw and isinstance(raw['post_clone'], list):
            self.post_clone = raw['post_clone']
        if 'post_pull' in raw and isinstance(raw['post_pull'], list):
            self.post_pull = raw['post_pull']
        if 'repos' in raw and isinstance(raw['repos'], dict):
            for child,child_info in raw['repos'].items():
                if child.endswith(os.sep):
                    child = child[:-1]
                if child in self.repos:
                    raise Exception(f"Found duplicate repo entry '{child}'")
                new_entry = Manifest.Repo()
                self.repos[child] = new_entry
                for key in new_entry.__dict__:
                    default_val = None
                    if key == 'branch':
                        default_val = 'master'
                    elif key == 'type':
                        default_val = 'repo'
                    new_entry.__dict__[key] = child_info.get(key, default_val)
                    mystery_keys = set(child_info.keys()) - set(new_entry.__dict__.keys())
                    if mystery_keys:
                        raise Exception(f"Unexpected hash keys detected within definition of {child}: {mystery_keys}")
                    if not new_entry.url and new_entry.type == 'repo':
                        raise Exception(f"Missing required key 'url' within definition of {child}")


#ANCHOR: Utility Methods
def gitp_operation(f):
    ''' Decorator for all command-line operations of `gitp`. Handles argument parsing and debug message verbosity. '''
    PARSERS[f.__name__] = argparse.ArgumentParser(formatter_class=argparse.RawTextHelpFormatter, prog=f'{os.path.basename(__file__)} {f.__name__}', add_help=True)
    def wrapper(*argv, **kwargs):
        preprocess_method = getattr(PARSERS[f.__name__], '_preprocess_method', None)
        if preprocess_method:
            preprocess_method(PARSERS[f.__name__])
        my_args, unknowns = PARSERS[f.__name__].parse_known_args()
        global DEBUG_LEVEL
        global FORCE_COLORS
        if my_args.verbosity != DEFAULT_DEBUG_LEVEL:
            DEBUG_LEVEL = my_args.verbosity
        if my_args.color != False:
            FORCE_COLORS = my_args.color
        sys.argv = [sys.argv[0], '-v', str(DEBUG_LEVEL), '--color', str(FORCE_COLORS)]
        return f(my_args, unknowns, *argv, **kwargs)
    wrapper.__doc__ = f.__doc__
    return wrapper

def style(string:str, style_types:typing.Union[list, Style], force:bool=False) -> str:
    '''
    Apply text styling to a given string. Does nothing if the `stdout` is not a terminal.

    Args:
        string: string to style
        style_types: a list of `Style` types to apply to `string`, or a single `Style` enum
        force: removes all existing styling prior to applying new style
    
    Returns:
        Styled string
    '''
    if not sys.stdout.isatty() and FORCE_COLORS != 'always':
        return string
    if force:
        string = re.sub('\\033\[\d+m(.*?)\\033\[0m', r'\1', string)
    if not isinstance(style_types, list):
        style_types = [style_types]
    for style_type in style_types:
        if style_type == Style.BOLD:
            string = f"\033[1m{string}\033[0m"
        if style_type == Style.ITALIC:
            string = f"\033[3m{string}\033[0m"
        if style_type == Style.BLUE:
            string = f"\033[94m{string}\033[0m"
        if style_type == Style.CYAN:
            string = f"\033[96m{string}\033[0m"
        if style_type == Style.GREEN:
            string = f"\033[92m{string}\033[0m"
        if style_type == Style.RED:
            string = f"\033[91m{string}\033[0m"
        if style_type == Style.YELLOW:
            string = f"\033[93m{string}\033[0m"
        if style_type == Style.GRAY:
            string = f"\033[97m{string}\033[0m"
        if style_type == Style.BLACK:
            string = f"\033[30m{string}\033[0m"
    return string

def debug(msg:str, level:int=0):
    '''
    Debug message print wrapper.

    Args:
        msg: message to print
        level: verbosity level of message
    '''
    if level <= DEBUG_LEVEL:
        print(msg)

def error(msg:str, level:int=0):
    '''
    Error message print wrapper.

    Args:
        msg: message to print
        level: verbosity level of message
    '''
    if level <= DEBUG_LEVEL:
        print(style(msg, Style.RED, force=True))
    global CLI_RETURN_CODE
    CLI_RETURN_CODE += 1
    
def get_repo_root(cwd:str=''):
    ''' 
    Queries `git` to get the root of the current git repo (throws an exception if `cwd` is not a git repo)

    Returns:
        Absoluite path to the root of the git repo associated with `cwd`
    '''
    try: 
        return _git('rev-parse --show-toplevel', cwd).strip()
    except: 
        raise Exception(f'Current directory ({os.path.abspath(cwd)}) is not a git repo.')

def _git(args:str, cwd:str=None, interactive:bool=False, out_post_process:typing.Callable[[str],str]=None) -> str:
    '''
    Utility method to execut a git command.

    Args: 
        args: arguments of the git command to run
        cwd: directory in which to execute the command
        interactive: interactive mode (stdin and stdout passthrough)
        out_post_process: string post processor for return value (run per-line of output)

    Returns:
        The stdout and stderr output of the command
    '''
    if sys.stdout.isatty() and FORCE_COLORS != 'always':
        args = '-c color.ui=always ' + args
    return _exec(['git'] + shlex.split(args), cwd, interactive, out_post_process)

def _exec(cmd:typing.List[str], cwd:str=None, interactive:bool=False, out_post_process:typing.Callable[[str],str]=None) -> str:
    '''
    Utility method to execute an arbitrary system command.

    Args:
        cmd: command to execute
        cwd: directory in which to execute the command
        interactive: interactive mode (stdin and stdout passthrough)
        out_post_process: string post processor for return value (run per-line of output)

    Returns:
        The stdout and stderr output of the command
    '''
    cwd = cwd or '.'
    cwdstr = '' if cwd == '.' else f'({cwd})>'
    debug(f'${cwdstr} {cmd}', level=3)

    #Babysit interactive command
    #FIXME: doesn't seem to work with interactive text editors
    if interactive:
        with Popen(cmd, cwd=cwd, stdin=sys.stdin, stdout=PIPE if out_post_process else sys.stdout, stderr=STDOUT) as p:
            ans = ''
            #STDOUT is piped -- handle output manually
            if out_post_process:
                newline = True
                while True:
                    exit = True if p.poll() is not None else False
                    #Perform non-blocking read
                    fd = p.stdout.fileno()
                    fl = fcntl.fcntl(fd, fcntl.F_GETFL)
                    fcntl.fcntl(fd, fcntl.F_SETFL, fl | os.O_NONBLOCK)
                    try:
                        out = p.stdout.read().decode('utf-8')
                    except:
                        out = ''
                    #Mirror stdout
                    else:
                        msg = out
                        if (newline or exit) and callable(out_post_process):
                            msg = out_post_process(msg)
                        newline = True if re.search(r'(?<!(\\))\n', out) else False
                        if newline or (not newline and out.strip() != ''): #this prevents corner cases from creeping in causing malformed output
                            print(msg, end='')
                        ans += msg
                    if exit:
                        if not ans.endswith('\n'):
                            print('')
                        break
                    time.sleep(0.1)
            #STDOUT is connected to sys.stdout -- wait for process to end
            else:
                p.wait()
        
            if p.returncode:
                raise subprocess.CalledProcessError(p.returncode, cmd, b'Failed running interactive command')
            return ans

    #Run non-interactive cmd in one go
    else:
        ans = subprocess.check_output(cmd, cwd=cwd, stderr=STDOUT).decode('utf-8')
        debug(ans.strip(), level=4)
        return '\n'.join([out_post_process(x) for x in ans.split('\n')]) if callable(out_post_process) else ans

def get_cmd_indenter(level:int=0) -> typing.Callable[[str], str]:
    '''
    Factory for indenter functions for pretty text output.
    
    Args:
        level: indent level for the produced indenter function
    
    Returns:
        Indenter formatting function with the specified indent level.
    '''
    indent = (''.join([' ' for _ in range(level)]) if level else '') + style('> ', Style.GRAY)
    def ans(out):
        return indent+('\n'+indent).join(out.strip().split('\n'))
    return ans

def is_real_dir(path:str) -> bool:
    '''
    Returns if the given path is a real directory (e.g. not linked).

    Args:
        path: path to check
    
    Returns:
        `True` if `path` is a real directory, `False` otherwise
    '''
    if not os.path.isdir(path):
        return False
    if path.endswith(os.sep):
        path = path[:-1]
    if os.path.islink(path):
        return False
    return True

MANIFEST_CACHE = {}
def get_manifest(root:str, create:bool=True)-> typing.Union[Manifest, None]:
    '''
    Finds the .gitp_manifest file and converts it into a dict.

    Args:
        root: path of top level to start search
        create: set to `True` to automatically create a .gitp_manifest file at `root` if it doesn't exist
    
    Returns:
        A `Manifest` object or `None` if a manifest does not exist.
    '''
    if root is None:
        return None
    path = os.path.join(root, '.gitp_manifest')
    if not os.path.isfile(path):
        try: 
            tgt_root = get_repo_root(root)
        except: 
            tgt_root = None
        if create and tgt_root is not None:
            if root != tgt_root:
                raise Exception(f"Attempted to create a .gitp_manifest file outside of the root directory of a .git repo!")
            debug(f'Creating {path}')
            with open(path, 'w') as f:
                f.write('')
            return Manifest(root, path)
        else:
            return None
    abs_path = os.path.abspath(path)
    last_modified = os.path.getmtime(abs_path)
    if abs_path in MANIFEST_CACHE and MANIFEST_CACHE[abs_path]['modified'] == last_modified:
        ans = MANIFEST_CACHE[abs_path]['content']
    else:
        try:
            ans = Manifest(root, path)
        except Exception as e:
            raise Exception(f"Failed to parse {path} due to malformed syntax:\n{e}")
        MANIFEST_CACHE[abs_path] = {
            'modified': last_modified,
            'content': ans,
        }
    return ans

def get_parent_manifest(root:str) -> typing.Union[Manifest, None]:
    '''
    Retrieves the parent repo's manifest, if one exists.

    Args:
        root: path to search from (child repo)

    Returns:
        Manifest object for parent repo, or None if no parent was found.
    '''
    try: 
        parent_root = get_repo_root(os.path.dirname(os.path.abspath(root)))
    except:
        return None
    return get_manifest(parent_root)

def check_for_changes(root:str, recurse:bool=True, ignore_committed:bool=False, ignore_uncommitted:bool=False, ignore_untracked:bool=False, ignore_local_branches:bool=False, **kwargs) -> typing.List[tuple]:
    '''
    Recursively checks a given repo structure for local changes.

    Args:
        root: path of top level repo to check
        recurse: recurse into child repos
        ignore_comitted: ignore committed changes (only report uncomitted/untracked changes)
        ignore_uncommitted: ignore uncommitted changes (only report local commits) 
        ignore_untracked: like `ignore_uncommitted`, but only ignore untracked changes (only report local commits)
        ignore_local_branches: ignore branches that are present locally but not in remote
    
    Returns:
        A list of tuples with [0]repo and [1] number of local commits, (including `root`). Repos without changes are not included on the list, but ones that do and have 0 local commits indicate local untracked changes.
    '''
    ans = kwargs.get('ans', None)
    if ans is None:
        ans = []
    if not os.path.isdir(root):
        return []
    #Check for unstaged changed and unpushed commits
    status_opts = ' -uno' if ignore_untracked else ''
    if (not ignore_uncommitted and _git(f'status --porcelain{status_opts}', root)) or (not ignore_committed and _git('log --branches --not --remotes', root)):
        commit_cnt = 0
        remotes = get_remotes(root)
        for remote in remotes:
            curr_branch = get_current_branch(root)
            try: cnt = int(_git(f'rev-list --count {remote}/{curr_branch}..', root))
            except: cnt = 0 #branch doesn't exist in remote
            if cnt > commit_cnt:
                commit_cnt = cnt
        if f'{remote}/{curr_branch}' not in _git('branch -r') and ignore_local_branches:
            pass
        else:
            ans.append((root, commit_cnt))
    m = get_manifest(root, create=False)
    if not m or not recurse:
        return ans
    for child in m:
        decendent_repo_path = os.path.join(root, child)
        check_for_changes(decendent_repo_path, ignore_committed=ignore_committed, ignore_uncommitted=ignore_uncommitted, ignore_untracked=ignore_untracked, ans=ans)
    return ans

def check_for_state_match(root:str, recurse:bool=True, filter_type:typing.Union[RepoState, typing.List[RepoState], None]=None, target:str=None, **kwargs):
    '''
    Recursively checks a given repo structure for parity with manifest file(s).

    Args:
        root: path of top level repo to check
        recurse: recurse into child repos
        filter_type: filter in state mismatch type(s)
        target: child repo to target (ignore checking of all other child repos)
    
    Returns:
        A dict decendent repos (including `root`) that are unaligned with manifest(s) with the following elements:
            - [key] path to offending child repo
            - [val][0]: actual branch
            - [val][1]: actual commit SHA
            - [val][2]: actual link (if linked)
            - [val][3]: state (NONEXISTENT, UNALIGNED, or UNLINKED)
    '''
    ans =  kwargs.get('ans', None)
    pfx = kwargs.get('pfx', '')
    if not isinstance(filter_type, list):
        filter_type = [] if filter_type is None else [filter_type]
    if ans is None:
        ans = {}
    m = get_manifest(root, create=False)
    if not m:
        return ans
    assert( (not recurse) or (recurse and (target is None))) #both cannot be set
    for child,child_info in m.items():
        if target is not None and child != target:
            continue
        decendent_repo_path = os.path.join(root, child)
        state = None
        curr_branch = None
        curr_commit = None
        curr_link = None
        if child_info.link:
            #Entirely missing
            if (not os.path.exists(decendent_repo_path) or len(os.listdir(decendent_repo_path)) == 0) and not os.path.islink(decendent_repo_path):
                state = RepoState.NONEXISTENT
            #Child is not linked
            elif is_real_dir(decendent_repo_path):
                state = RepoState.UNLINKED
            #Symbolic link exists, but points to something foreign
            elif os.path.islink(decendent_repo_path) and os.readlink(decendent_repo_path) != child_info.link:
                state = RepoState.UNALIGNED
                curr_link = os.readlink(decendent_repo_path)
        else:
            #Not cloned
            if not is_real_dir(decendent_repo_path):
                state = RepoState.NONEXISTENT
            elif len(os.listdir(decendent_repo_path)) == 0:
                state = RepoState.NONEXISTENT
            #Cloned, but commit or branch is mismatched
            else:
                try:
                    curr_branch = get_current_branch(decendent_repo_path)
                except:
                    pass
                curr_commit = get_current_commit(decendent_repo_path)
                exp_commit = child_info.commit
                exp_branch = None if exp_commit else child_info.branch
                if (exp_branch is not None and curr_branch != exp_branch) or (exp_commit is not None and not curr_commit.startswith(exp_commit)):
                    state = RepoState.UNALIGNED
        if state is not None and (not filter_type or state in filter_type):
            ans[os.path.join(pfx, child) if pfx else child] = (curr_branch, curr_commit, curr_link, state)
        if recurse:
            check_for_state_match(decendent_repo_path, filter_type=filter_type, ans=ans, pfx=child)
    return ans

def check_for_overlay_state_match(tgt:str, overlays:typing.Dict[str, Manifest.Repo], overlay_root:str) -> typing.Tuple[RepoState, str, str]:
    '''
    Checks if a given overlay link is in sync with the manifest.

    Args:
        tgt: target link to check
        overlays: dict mapping overlay paths to their repo objects
        overlay_root: root path associated with `overlays` (repo root)
    
    Returns:
        Tuple of [0] `RepoState` enumeration based on the alignment between `tgt` and `overlays`, and [1] current link path of `tgt`, and [2] expected link path of `tgt`
    '''
    curr_link = None
    status = RepoState.CLEAN
    if is_real_dir(tgt):
        status = RepoState.UNLINKED
    elif os.path.islink(tgt):
        #Compare abs path of both configured and actual links
        curr_link = os.path.relpath(os.path.abspath(os.path.join(os.path.dirname(tgt), os.readlink(tgt))), overlay_root) if not os.path.isabs(os.readlink(tgt)) else os.readlink(tgt)
        link = os.path.relpath(os.path.abspath(os.path.join(overlay_root, overlays[tgt].link)), overlay_root) if not os.path.isabs(overlays[tgt].link) else overlays[tgt].link
        if curr_link != link:
            status = RepoState.UNALIGNED
        else:
            status = RepoState.OVERLAYED
    else:
        status = RepoState.NONEXISTENT
    return status, curr_link, link

def merge_in_progress(root:str) -> bool:
    '''
    Returns whether or not a git merge is currently in progress.

    Args:
        root: path of repo to check

    Returns:
        `True` if `root` is currently in the middle of a merge, `False` otherwise.
    '''
    try: 
        _git('merge HEAD', root)
        return False
    except:
        return True

def repo_lock(root:str):
    '''
    Acquire lock on a repo. Usage:

    ```
    with repo_lock('my/repo/path'):
        #some atomic operation
    ```

    Args:
        repo: path of repo to acquire lock for
    '''
    return FileLock(os.path.join(root, '.git', 'index.lock'))

def get_current_branch(root:str) -> str:
    '''
    Returns the current branch. 
    
    Args:
        root: path of repo to check
    
    Returns:
        Name of the current branch. If not on a branch (i.e. detached HEAD state), returns empty string.
    '''
    try: return _git('symbolic-ref -q HEAD', root).rsplit('/', 1)[-1].strip()
    except: return ''

def get_current_commit(root:str) -> str:
    '''
    Returns the commit at HEAD. 
    
    Args:
        root: path of repo to check
    
    Returns:
        Commit SHA corresponding to HEAD.
    '''
    return _git('rev-parse HEAD', root).strip()

def get_remotes(root:str) -> typing.Dict[str, str]:
    '''
    Returns the remote URL mappings for a given repository.

    Args:
        root: path of repo to check
    
    Returns:
        A dict that maps remote names (e.g. `"origin"`) to a dict of their respective URLs (e.g. `{"fetch": "some_fetch_remote_url", "push": "some_push_remote_url"}`)
    '''
    try: 
        out = _git('remote -v', root)
        ans = {}
        for line in out.split('\n'):
            if line.strip() == '':
                continue
            name,url,type = line.split()
            if name not in ans:
                ans[name] = {'fetch': None, 'push': None}
            ans[name][type[1:-1]] = url
        return ans
    except subprocess.CalledProcessError as e:
        raise Exception(f"Unable to obtain remote information for {root} due to the following reason(s):\n{e.output.decode('utf-8')}")
    except Exception as e:
        raise Exception(f"Got an unexpected output format during querying remote information for {root}: {e}")

def resolve_repo_link(root:str, entry:Manifest.Repo, fail:bool=True) -> typing.Union[str, None]:
    '''
    Return an absolute path of a Repo object's link. Handles link filtering and searching for newest subdir.

    Args:
        root: root path of where to apply link
        entry: Repo object
        fail: `True` to raise exceptions if no eligible subdir can be found (assuming newest subdir searching is enable in the entry)

    Returns:
        The absolute path of the link, or None if no match found.
    '''
    link = entry.link
    qualified_link = os.path.join(root, link) if not os.path.isabs(link) else link
    #Search for last created subdirectory to link to
    if entry.link_newest:
        if not os.path.isdir(qualified_link):
            if fail:
                raise Exception(f"Link search directory '{qualified_link}' does not exist")
            return None
        qualified_link = get_latest_subdir(qualified_link, entry.link_filter)
        if qualified_link is None:
            if fail:
                raise Exception(f"Could not find any valid subdirectory within {entry.link} to link to")
            return None
    return qualified_link

def get_latest_subdir(root:str, regex_str:str=None) -> typing.Union[str, None]:
    '''
    Finds the last modified subdirectory within some specified directory.

    Args:
        root: directory in which to search
        regex_str: a regex string to filter in search results
    
    Returns:
        The absolute path to the last modified subdirectory within `root` that matches the specified `regex_str`, is supplied. `None` if no match was found.
    '''
    try: regex = re.compile(regex_str) if regex_str else None
    except Exception as e:
        raise Exception(f"Invalid regular expression '{regex_str}' specified: {e} ") from None
    if not os.path.isdir(root):
        raise Exception(f"Specified directory '{root}' does not exist")
    subdirs = list(filter(os.path.isfile, glob.glob(root + '*'))).sort(key=lambda x: os.path.getmtime(x)) #sort subdirs from newest to oldest
    for subdir in subdirs:
        if regex is None or re.search(regex, subdir):
            return subdir                    
    return None
    
def apply_overlays(root:str, overlay_entries:typing.Dict[str, Manifest.Repo], targets:typing.List[str]=None, force:bool=False):
    '''
    Apply overlay links from the cwd.

    Args:
        root: root directory associated with `overlay_entries`
        overlay_entries: overlay entries for repo associated with cwd
        targets: target paths to apply overlay to
        force: force overlay if there are conflicts
    '''
    for abspath,child_info in overlay_entries.items():
        relpath = os.path.relpath(abspath, os.getcwd())
        #Don't create a link if we specified target arg and this overlay isn't part of that target
        if targets and not [x for x in targets if abspath.startswith(os.path.abspath(x))]:
            continue
        link = resolve_repo_link(root, child_info, fail=not force)
        if not os.path.isabs(child_info.link):
            link = os.path.relpath(os.path.join(root, link), os.path.dirname(relpath))
        debug(f"Applying overlay {style(child_info.link, Style.BOLD)} on top of {style(relpath, Style.BOLD)}")
        #Check if target of overlay exists and has local changes
        if is_real_dir(relpath) and check_for_changes(relpath) and not force:
            raise Exception(f"Failed to apply overlay on top of {relpath} due to there being local changes present (push or specify --force to permenantly clobber)")
        if _git('stash list', relpath) and not force:
            raise Exception(f"Failed to apply overlay on top of {relpath} due to there being locally stashed changes present (push or specify --force to permenantly clobber)")
        if os.path.islink(relpath):
            os.unlink(relpath)
        elif os.path.isdir(relpath):
            shutil.rmtree(relpath)
        os.symlink(link, relpath)

def args_to_str(args:typing.List[str]) -> str:
    '''
    Converts a parsed list of arg strings back into a serialized string.

    Args:
        args: list of arg strings
    
    Returns:
        A string of all args.
    '''
    return ' '.join([x if ' ' not in x else '"' + x.replace('"', '\"') + '"' for x in args])

def gitignore_add(root:str, token:str, check_exists:bool=False):
    '''
    Add an entry to a .gitignore file.

    Args:
        root: directory containing .gitignore
        token: token to add
        check_exists: check if token already exists and skip adding it of it does
    '''
    gitignore_path = os.path.relpath(os.path.join(root, '.gitignore'), os.getcwd())
    if check_exists:
        with open(gitignore_path, 'r') as f:
            for line in f.readlines():
                if line.strip() == token:
                    return 
    debug(f"Updating {gitignore_path}")
    with open(gitignore_path, 'a') as f:
        f.write('\n' + token)

def gitignore_rm(root:str, token:str):
    '''
    Remove an entry from a .gitignore file.

    Args:
        root: directory containing .gitignore
        token: token to remove
    '''
    gitignore_path = os.path.relpath(os.path.join(root, '.gitignore'), os.getcwd())
    debug(f"Updating {gitignore_path}")
    with open(gitignore_path, 'r') as f:
        new_content = []
        for line in f:
            if line.strip() == token:
                continue
            new_content.append(line.strip())
    with open(gitignore_path, 'w') as f:
        f.write('\n'.join(new_content))

def abbreviate_status_print(out:str) -> str:
    '''
    Abbreviates a `git status` print message to be more concise.

    Args:
        out: raw `git status` message
    
    Returns:
        Concise version of `git status` message.
    '''
    uptodate = 'Your branch is up to date with' in out
    out = out.split('\n')
    if uptodate:
        out = out[2:]
    out = [x for x in out if DEBUG_LEVEL or not ('On branch ' in x or '(use "git' in x or 'nothing to commit,' in x or 'Your branch is ahead' in x) and x]
    out = '\n'.join([style(x, Style.GRAY) for i,x in enumerate(out) if i not in [0, len(out)-1] or x != ''])
    return out

def obtain_server_lock(host_info:typing.Tuple[str,int], func:typing.Callable):
    '''
    Communicates with a given gitp lock server to obtain a lock for atomic operations.

    Args:
        host_info: a tuple containing [0] the hostname and [1] port of the remote server
        func: function to run after lock is obtained
    '''
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.connect(host_info)
    try:
        #Wait in line for the lock
        req_id = None
        obtained_lock = False
        while not obtained_lock:
            response = server.recv(255).decode('utf-8')
            if response == '':
                raise Exception()
            responses = response.split('.')
            for r in responses:
                try:
                    if req_id is None:
                        req_id = int(r)
                        debug(style(f"Submitted lock request to {host_info[0]}:{host_info[1]} (ID:{req_id})", Style.GRAY))
                    elif re.search(r'^0:\d+$', r):
                        timeout = int(r.split(':')[-1])
                        debug(style(f"Obtained lock, setting local timeout to {timeout}", Style.GRAY))
                        obtained_lock = True
                        break
                    elif r == '':
                        continue
                    else:
                        place_in_line = int(r)
                        debug(style(f"#{place_in_line} in the lock queue", Style.GRAY))
                except:
                    raise Exception(f"Malformed response from lock server {host_info[0]}:{host_info[1]}: '{r}'")

        #Execute function
        t = threading.Thread(target=func, daemon=True)
        try:
            t.start()
            t.join(timeout)
            if t.is_alive():
                raise Exception(f"Operation timed out after {timeout} second(s). This may cause corrupt repo state(s) -- please rerun the last command.")
        finally:
            server.send('done'.encode('utf-8'))
            server.close()

    except KeyboardInterrupt:
        debug(f"Aborting operation. This may cause corrupt repo state(s) -- please rerun the last command.")
        server.close()
        global CLI_RETURN_CODE
        CLI_RETURN_CODE = 1
        return

def quote_exec_cmd(parser:argparse.ArgumentParser):
    '''
    Argparse utility to quote the command specified to avoid argparse confusion, separate the gitp options out.

    Args:
        parser: argparse parser object to modify
    '''
    args = sys.argv[1:]
    plain_args = []
    operation_opts = []
    i = 0
    while i < len(args):
        added = False
        for optional in parser._optionals._actions:
            if args[i] in optional.option_strings:
                operation_opts.append(args[i])
                if i + 1 < len(args) and isinstance(optional, (argparse._StoreAction, argparse._AppendAction)):
                    operation_opts.append(args[i + 1])
                    i += 1
                added = True
                break
        if not added:
            plain_args.append(args[i])
        i += 1
    args = ' '.join(plain_args)
    args = [args] if args else []
    sys.argv = [sys.argv[0]] + operation_opts + args


'''
# `gitp` commands

| Cmd             | Description                                                      |
|-----------------|------------------------------------------------------------------|
| sync            | Synchronize repo w/ manifest (don't pull, just clone missing)    |
| link            | Applys linking to a child repo                                   |
| unlink          | Unapplies a link from a child repo                               |
| new             | Add a new child repo                                             |
| exec            | Execute arbitrary commands recursively                           |
| server          | Run a gitp lock server                                           |

# `git` command overrides

| Done | Intercept | Batch | Cmd             | Description                                                      |
|------|-----------|-------|-----------------|------------------------------------------------------------------|
|  ✓   | c         |       | clone           | Clone a repository into a new directory                          |
|  -   |           |       | init            | Create an empty Git repository or reinitialize an existing one   |
|  ✓   | s         |   x   | add             | Add file contents to the index                                   |
|  ✓   | s         |       | mv              | Move or rename a file, a directory, or a symlink                 |
| TODO |           |       | restore         | Restore working tree files                                       |
|  ✓   | c         |       | checkout        | Alias for restore/switch                                         |
|  ✓   | c         |       | rm              | Remove files from the working tree and from the index            |
|  -   |           |       | revert          | Revert a commit                                                  |
|  -   |           |       | sparse-checkout | Initialize and modify the sparse-checkout                        |
|  -   |           |       | bisect          | Use binary search to find the commit that introduced a bug       |
| TODO | c         |   x   | format-patch    | Create a patch file for changesets                               |
| TODO | c         |   x   | apply           | Apply patch of changesets                                        |
| TODO | s         |   x   | diff            | Show changes between commits, commit and working tree, etc       |
| TODO | s         |   x   | grep            | Print lines matching a pattern                                   |
|  -   |           |       | log             | Show commit logs                                                 |
|  ✓   | s         |   x   | show            | Show various types of objects                                    |
|  ✓   | c         |   x   | status          | Show the working tree status                                     |
|  ✓   | s         |   x   | stash           | Stash the changes in a dirty working directory away              |
|  ✓   | s         |   x   | branch          | List, create, or delete branches                                 |
|  ✓   | s         |   x   | commit          | Record changes to the repository                                 |
| TODO | c         |   x   | merge           | Join two or more development histories together                  |
| TODO | c         |   x   | rebase          | Reapply commits on top of another base tip                       |
|  ~   | c         |       | reset           | Reset current HEAD to the specified state                        |
| TODO |           |       | switch          | Switch branches                                                  |
|  ✓   | s         |   x   | tag             | Create, list, delete or verify a tag object signed with GPG      |
|  ✓   | s         |   x   | fetch           | Download objects and refs from another repository                |
|  ✓   | c         |   x   | pull            | Fetch from and integrate with another repository or a local branch |
|  ✓   | s         |   x   | push            | Update remote refs along with associated objects                 |
|  ✓   | c         |       | remote          | Manage set of tracked repositories                               |

c=complex implementation
s=simple implementation
'''

#ANCHOR: remote()
@gitp_operation
def remote(args, unknowns=None):
    #Passthrough if query or adding remotes
    if 'set-url' not in unknowns or '--push' in unknowns:
        global GIT_FALLBACK
        GIT_FALLBACK = True
        return
    #Update parent manifest if url of 'origin' is updated
    try: 
        out = _git('remote ' + args_to_str(unknowns))
        if out: debug(out)
    except subprocess.CalledProcessError as e:
        raise Exception(e.output.decode('utf-8'))
    if unknowns[-2] == 'origin':
        root = get_repo_root()
        m = get_parent_manifest(root)
        if m:
            child = os.path.relpath(root, os.path.dirname(m.path))
            if child in m:
                m[child].url = unknowns[-1]
                m.write()


#ANCHOR: fetch()
@gitp_operation
def fetch(args, unknowns=None):
    orig_argv = sys.argv
    sys.argv = [sys.argv[0], ('git fetch ' + args_to_str(unknowns)).strip()]
    m = get_manifest(get_repo_root(os.getcwd()), create=False)
    w = functools.partial(exec, force_color=True)
    if m and m.lock_server:
        obtain_server_lock(m.lock_server, w)
    else:
        w()
    sys.argv = orig_argv


#ANCHOR: show()
@gitp_operation
def show(args, unknowns=None):
    orig_argv = sys.argv
    sys.argv = [sys.argv[0], ('git show ' + args_to_str(unknowns)).strip()]
    exec()
    sys.argv = orig_argv


#ANCHOR: stash()
@gitp_operation
def stash(args, unknowns=None):
    if '--patch' in unknowns or '-p' in unknowns:
        raise Exception("Cannot run with --patch interactive option") #FIXME: should this be supported?

    root = get_repo_root()
    stashes_file = os.path.join(root, '.gitp_stashes')
    op = 'push' if not unknowns else unknowns[0]
    opts = [x for x in unknowns if x.startswith('-')]
    tgt_stash = None
    branchname = ''
    if len(unknowns) > 1:
        revs = [x.strip() for x in unknowns[1:] if not x.startswith('-')]
        if revs:
            tgt_stash = revs[0]
            if op == 'branch' and len(revs) == 2:
                branchname = revs[0]
                tgt_stash = [1]
            elif len(revs) > 1: #let git error out
                global GIT_FALLBACK
                GIT_FALLBACK = True
                return

    def update_stash_pos(lines:typing.List[str]):
        for i,line in enumerate(lines): 
            lines[i] = re.sub(r'stash@{(\d+)}(.*)', r'stash@{' + str(i) + r'}\2', line)

    def is_gitp_stash(line:str, gitp_id:typing.Union[int,None]=None):
        '''
        Parse a stash list line and discern if it is a (particular) gitp stash item.

        Args:
            line: string to parse
            gitp_id: special id assigned by gitp that gets encoded within the stash msg

        Returns:
            A dict containing stash stack pos, id, branch, and msg information if the line matched. Otherwise, return `False`.
        '''
        m = re.search(r'stash@{(\d+)}:\sOn\s(.+?):\s(.*)', line)
        if m:
            ans_pos,ans_branch,ans_msg = m.groups(0)
            ans_id = re.search(r'^__gitp(\d+).*', ans_msg)
            if not ans_id:
                return False
            ans_id = ans_id.groups(0)[0]
            if gitp_id is not None and gitp_id != ans_id:
                return False
            return {'pos': ans_pos, 'branch': ans_branch, 'msg': ans_msg, 'id': ans_id}
        return False

    #Recursively check stack state -- if we find a matching stack entry (i.e. message and id match), execute the pop/apply command
    def work(root:str, op:str, rd_tgts=None, branchname:str='', wr_tgt:typing.Union[str,None]=None, level:int=0):
        ans = False
        relpath = os.path.relpath(root, os.getcwd())
        if relpath == '.':
            relpath = root
        indent = ''.join([' ' for _ in range(level)]) + '∟ ' if level else ''
        cmd_indenter = get_cmd_indenter(level + 2)
        try: 
            orig_ref = None
            if wr_tgt: #push to stack
                out = _git(f'stash {args_to_str(opts)} -m "' + wr_tgt.replace('"', '\"') +'"', root)
                if re.search('No local changes to save', out):
                    debug(f"{indent}{style(relpath, Style.BOLD)} : nothing to stash")
                else:
                    ans |= True
                    debug(f"{indent}{style(relpath, Style.BOLD)} : wrote stack")
                    debug(style(cmd_indenter(out), Style.GRAY, force=True))
            else: #read stack
                out = _git('stash list', root)
                matches = []
                for line in out.split('\n'):
                    for tgt in rd_tgts:
                        entry = is_gitp_stash(line, tgt['id'])
                        if entry: matches.append(entry['pos'])
                if not matches:
                    debug(f'{indent}{style(relpath, Style.BOLD)} : no matching stash, skipping')
                else:
                    if op == 'branch':
                        orig_ref = get_current_branch(root) or get_current_commit(root)
                    op_str = 'drop' if op == 'clear' else op #`clear` turns into dropping selective stashes
                    out = ''
                    for pos in matches:
                        out += _git(f"stash {op_str} {args_to_str(opts)} {branchname} {'stash@{' + pos + '}'}", root)
                        ans |= True
                    debug(f"{indent}{style(relpath, Style.BOLD)} : read stack")
                    out = abbreviate_status_print(out)
                    debug(cmd_indenter(out))
            m = get_manifest(root)
            if m:
                for child in m:
                    ans |= work(os.path.join(root, child), op, rd_tgts, branchname, wr_tgt, level=level+2)
        except subprocess.CalledProcessError as e:
            #Clean up by restoring original branch and deleting the new one
            if orig_ref:
                try:
                    _git(f"checkout {orig_ref}", root)
                    _git(f"branch -d {branchname}", root)
                except:
                    pass
            raise Exception(f"Failed to execute stash command for the following reason(s): {e.output.decode('utf-8').strip()}")
        return ans
           
    #Create/register .gitp_stashes file if it doesn't exist
    gitignore_add(root, '.gitp_stashes', check_exists=True)
    if not os.path.isfile(stashes_file):
        with open(stashes_file, 'w') as f:
            f.write('')

    #Read .gitp_stashes file
    with open(stashes_file, 'r') as f:
        lines = f.readlines()
    
    #Validate and parse stash file content
    tgt_stashes = []
    reserved_ids = {}
    stash_matched = False if tgt_stash else True
    for i,line in enumerate(lines):
        #Obtain stash info
        entry = is_gitp_stash(line)
        if not entry:
            raise Exception(f"Line {i + 1} within .gitp_stashes malformed (likely due to corrupted file): '{lines[i].strip()}'")

        #Track all existing gitp stash ids
        if entry['id'] in reserved_ids:
            raise Exception(f"Corrupted .gitp_stashes file contains duplicate stash entry: {entry['msg']}")
        reserved_ids[entry['id']] = True

        #Capture operating set
        if op == 'clear': #operate on all entries of the stack
            tgt_stashes.append(entry)
        if not tgt_stash and not tgt_stashes: #operate on the top of the stack by default
            tgt_stashes.append(entry)
        elif tgt_stash == entry['msg'] or tgt_stash == 'stash@{' + str(entry['pos']) + '}': #operate on a specific stack entry
            stash_matched = True
            tgt_stashes.append(entry)
    if not stash_matched:
        raise Exception(f"Unknown stash '{tgt_stash}' specified")

    #Simply list the content of .gitp_stashes
    if op == 'list':
        msg = ''.join(lines).strip()
        if msg:
            print(msg)

    #Stack read operation
    elif unknowns and op in ['show', 'drop', 'pop', 'apply', 'branch', 'clear']:
        if not tgt_stashes:
            debug(f"No stash entries found.")
            return
        work(root, op, rd_tgts=tgt_stashes, branchname=branchname)

        #Commit change to stashes file (remove popped/dropped stashes)
        if op in ['drop', 'pop', 'clear', 'branch']:
            with open(stashes_file, 'w') as f:
                new_lines = []
                for line in lines:
                    if is_gitp_stash(line)['msg'] not in [x['msg'] for x in tgt_stashes]:
                        new_lines.append(lines)
                update_stash_pos(new_lines)
                f.writelines(new_lines)

    #Stack write operation
    elif op in ['push']:
        stash_name = datetime.datetime.now().strftime('%y%m%d%H%M%S')
        tentative_id = stash_name
        i = 0
        while tentative_id in reserved_ids:
            tentative_id = stash_name + str(i)
            i += 1
        stash_name = f'__gitp{tentative_id} {args.msg}'
        curr_branch = get_current_branch(root) or '(no branch)'

        #Push to top of stack
        lines.insert(0, 'stash@{0}: On ' + curr_branch + f': {stash_name}\n')
        update_stash_pos(lines)

        #Recursively stash changes w/ generated stash message, if any exist
        something_stashed = work(root, op, wr_tgt=stash_name)
        if not something_stashed:
            print('No local changes to save')
            return

        #Commit change to stashes file
        debug(f'Stashed {stash_name}')
        with open(stashes_file, 'w') as f:
            f.writelines(lines)

PARSERS['stash'].add_argument('-m', '--message', dest='msg', action='store', default='', help='Stash message (for push operation)')


# #ANCHOR: switch()
# @gitp_operation
# def switch(args, unknowns=None):
#     #Switch is simply a subset of `checkout`
#     sys.argv = [sys.argv[0]]
#     if args.new_branch:
#         sys.argv.append('-b') #-c/-C is functionally equivallent to `checkout`'s -b
#     sys.argv += unknowns
#     checkout()
# PARSERS['switch'].add_argument('-c', '-C', dest='new_branch', action='store_true', default=False, help='Create and checkout a new branch')


# #ANCHOR: restore()
# @gitp_operation
# def restore(args, unknowns=None):
#     #Restore is simply a subset of `checkout`
#     sys.argv = [sys.argv[0]]
#     sys.argv += unknowns
#     checkout()


#ANCHOR: reset()
@gitp_operation
def reset(args, unknowns=None):
    #TODO:
    #re-sync child repos after reset takes place
    #handle reset of descendent repo content
    pass


#ANCHOR: tag()
@gitp_operation
def tag(args, unknowns=None):
    if '-a' in unknowns and '-m' not in unknowns:
        raise Exception(f'Must specify -m with -a (cannot execute interactive command)')
    orig_argv = sys.argv
    sys.argv = [sys.argv[0], ('git tag ' + args_to_str(unknowns)).strip()]
    exec()
    sys.argv = orig_argv


#ANCHOR: branch()
@gitp_operation
def branch(args, unknowns=None):
    if '--edit-description' in unknowns:
        raise Exception("Cannot run with --edit-description interactive option") #FIXME: should this be supported?

    #Simply run branch commands recursively
    orig_argv = sys.argv
    sys.argv = [sys.argv[0], ('git branch ' + args_to_str(unknowns)).strip()]
    exec()
    sys.argv = orig_argv


#ANCHOR: commit()
@gitp_operation
def commit(args, unknowns=None):
    if ('--no-edit' not in unknowns and '-m' not in unknowns and '-C' not in unknowns and '-F' not in unknowns and '--file' not in unknowns) or '-p' in unknowns or '-e' in unknowns:
        raise Exception(f'Must specify -m, -C, or -F (cannot execute interactive command)')

    def work(root:str, overlays:typing.Dict[str,Manifest.Repo]=None, level:int=0):
        indent      = ''.join([' ' for _ in range(level)]) + '∟ ' if level else ''
        cmd_indenter = get_cmd_indenter(level + 2)

        #Only commit if there are locally staged changes
        out = _git('diff --cached', root).strip()
        if out:
            debug(f"{indent}Committing changes in {style(os.path.relpath(root, os.getcwd()), Style.BOLD)}")
            try:
                out = _git(f'commit {args_to_str(unknowns)}', root).strip()
                debug(style(cmd_indenter(out), Style.GRAY, force=True))
            except subprocess.CalledProcessError as e: #if we fail, report it and keep moving
                error(cmd_indenter(e.output.decode('utf-8').strip()))
        else:
            debug(f"{indent}No changes staged in {style(os.path.relpath(root, os.getcwd()), Style.BOLD)}")
        
        #Recurse into children
        m = get_manifest(root, create=False)
        if m and overlays is None:
            overlays = m.get_overlays()
        if m:
            for child in m:
                if child not in overlays:
                    work(os.path.join(root, child), overlays=overlays, level=level+2)
    root = get_repo_root()
    work(root)


#ANCHOR: pull()
@gitp_operation
def pull(args, unknowns=None, root:str='', standalone:bool=True, utility_cmd_level:typing.Union[None,int]=None):
    '''
    Fetch from and integrate with another view or a local branch.
    '''
    overlay_entries = {}
    top_root = get_repo_root(root)

    def work(src:typing.Union[str,None], dst:str, parent_path:str, pm:typing.Union[Manifest,None]=None, name:typing.Union[str,None]=None, tgt:typing.Union[str,None]=None, level:int=0):
        '''
        src:            source of pull (might be `None`, in which case we pull from remote)
        dst:            destination of pull (repo to pull changes into)
        parent_path:    path to parent root directory
        pm:             parent manifest entry for this repo read from the destination repo (might be `None` if `dst` is a top level repo)
        name:           child name of `dst` repo (might be `None` if `dst` is a top level repo)
        tgt:            specific path to process (ignore all other heirarchy content -- ending in '/' means recursive)
        level:          level relative to original invocation of this recursive method (increasing from 0)
        '''
        nonlocal utility_cmd_level
        nonlocal overlay_entries
        nonlocal top_root
        os.environ['GITP_PARENT_REPO'] = '1' if top_root == dst else '0'

        #Format strings used for pretty output
        indent      = ''.join([' ' for _ in range(level)]) + '∟ ' if level else ''
        cmd_indenter = get_cmd_indenter(level + 2)

        #Basic dst repo info
        relpath     = os.path.relpath(dst, os.getcwd())
        label       = os.path.basename(dst) if top_root == dst else relpath
        url         = pm[name].url if pm is not None else list(get_remotes(dst).values())[0]['fetch']
        branch      = pm[name].branch if pm is not None else get_current_branch(dst)
        commit      = pm[name].commit if pm is not None else ''
        link        = pm[name].link if pm is not None else ''
        clone_src   = url
        pull_src    = ''

        #Don't bother syncing repos that are going to get overlayed
        if dst in overlay_entries and not args.local:
            if (not tgt or tgt.endswith(os.sep)) and (top_root != parent_path):
                debug(f"{indent}Skipping {style(label, Style.BOLD)} (target of an overlay)")
            return

        #Filter by target -- skip to the target repo(s), leaving everything else untouched
        skip = False
        if tgt:
            #not ending in / means just the sole repo (except for --local clones of links)
            if not tgt.endswith(os.sep) and not (link and args.local):
                if os.path.join(top_root, tgt) != dst:
                    skip = True
            #ending in / means include descendents (include the top dir and all descendents of that dir)
            elif dst != os.path.join(top_root, tgt).rstrip(os.sep) and not dst.startswith(os.path.join(top_root, tgt)):
                skip = True

        #Process ourselves first
        operation = 'Skipping'
        if not skip:

            #Point source of operation to src repo if one is supplied
            if src is not None:
                #Even if the source repo state is invalid, we can pull from the aligned branch that we care about (and we always do this for links)
                pull_src = clone_src = os.path.relpath(os.path.abspath(src), os.path.abspath(relpath)) if not os.path.isabs(src) else src
            forced_msg = ''
            cleanup_dir = True if not os.path.isdir(dst) else False
            
            #Obtain the data by either pulling, checking out, linking, or cloning depending on what's present locally
            #Repo dir exists (non-empty dir or a symlink): perform a pull
            if (is_real_dir(relpath) and len(os.listdir(relpath))) or os.path.islink(relpath):
                operation = 'Pulling'
                if commit or link and is_real_dir(relpath):
                    stashed_changes = False
                    if link: #linking will cause destruction of existing real dir -- don't skip any changes
                        changes = check_for_changes(relpath)
                        stashed_changes = _git('stash list', relpath)
                    else: #pulling will simply merge into existing real dir -- skip committed changes
                        changes = check_for_changes(relpath, ignore_committed=True, ignore_untracked=True)
                    changes = [x[0] for x in changes]
                    if changes or stashed_changes:
                        if not args.force:
                            if changes:
                                raise Exception(f"There are local changes within the following repos: {changes} (specify --force to permenantly clobber)")
                            if stashed_changes:
                                raise Exception(f"There are locally stashed changes within the following repos: {changes} (specify --force to permenantly clobber)")
                        else:
                            forced_msg = style(' (clobbering)', Style.RED)

            #If repo dir doesn't exist/is empty directory, perform a clone
            else:
                operation = 'Cloning'

            if link:
                operation = 'Copying' if args.local else 'Linking'
                if utility_cmd_level is None: #skip printing this header if we are being used as a utility cmd for another operation
                    debug(f"{indent}{operation} {style(label, Style.BOLD)}{forced_msg}")
                #Form target link  
                qualified_link = resolve_repo_link(parent_path, pm[name], fail=not args.force)
                if not os.path.isdir(qualified_link) and not args.force:
                    raise Exception(f"Specified link '{link}' for {relpath} does not exist (specify with --force to ignore)")
                #Create link
                if qualified_link:
                    if is_real_dir(relpath):
                        shutil.rmtree(relpath)
                    elif os.path.islink(relpath):
                        os.unlink(relpath)
                    if args.local:
                        shutil.copytree(qualified_link, relpath)
                    else:
                        if not os.path.isabs(link):
                            link = os.path.relpath(qualified_link, parent_path)
                        os.symlink(link, relpath)
            else:
                if utility_cmd_level is None: #skip printing this header if we are being used as a utility cmd for another operation
                    debug(f"{indent}{operation} {style(label, Style.BOLD)}{forced_msg}")
                #Clobber any existing link that conflicts
                if os.path.exists(relpath) and os.path.islink(relpath):
                    os.unlink(relpath)
                os.makedirs(relpath, exist_ok=True)
                #Update or clone the repo
                try: 
                    if operation == 'Pulling':
                        try:
                            out = _git(f'pull {args_to_str(unknowns)} {pull_src} {branch if pull_src else ""}', dst).strip()
                            debug(style(cmd_indenter(out), Style.GRAY, force=True))
                        except subprocess.CalledProcessError as e: #if we fail, report it and keep moving
                            out = e.output.decode('utf-8').strip()
                            error(cmd_indenter(out))

                    elif operation == 'Cloning':
                        out = _git(f'clone {clone_src} .', dst).strip()
                        debug(style(cmd_indenter(out), Style.GRAY, force=True))
                        #Clean up remote URL(s) (FIXME: this diverges from the normal git behavior -- is it ok?)
                        if url != clone_src:
                            remotes = get_remotes(src)
                            for remote,urls in remotes.items():
                                try: 
                                    _git(f'remote set-url {remote} {urls["fetch"]}', dst)
                                    if urls["fetch"] != urls["push"]:
                                        _git(f'remote set-url --push {remote} {urls["push"]}', dst)
                                except subprocess.CalledProcessError as e:
                                    raise Exception(f"Failed to update remote url {remote} of {dst} due to the following reason(s):\n{e.output.decode('utf-8')}")
                            try: 
                                _git(f'fetch', dst) #fetch from original remote to get back to same (relative) state as src of clone
                            except subprocess.CalledProcessError as e:
                                raise Exception(f"Failed to clone {dst} due to the following reason(s):\n{e.output.decode('utf-8')}")

                    if commit:
                        out = _git(f'reset --hard {branch}', dst).strip()
                        debug(style(cmd_indenter(out), Style.GRAY, force=True))
                    else:
                        out = _git(f'checkout {branch}', dst).strip()
                        debug(style(cmd_indenter(out), Style.GRAY, force=True))
                except subprocess.CalledProcessError as e:
                    if cleanup_dir:
                        shutil.rmtree(dst)
                    raise Exception(f"Failed to update {label} for the following reason(s)\n{e.output.decode('utf-8')}")

        #We've got children -- recurse
        m = get_manifest(dst, create=False)

        if m:
            #Get overlays (those defined beneath top level are completely ignored)
            if top_root == dst: 
                overlay_entries = m.get_overlays()

            #Process children
            if not link:
                #Do this work recursively (but don't recurse into linked repos or in the case of a repo to repo pull, source repos that are not manifest-aligned)
                for child in m:
                    new_src = os.path.join(src, child) if src is not None else src
                    work(src=new_src, dst=os.path.join(dst, child), parent_path=dst, pm=m, name=child, tgt=tgt, level=level+2 if not skip else level)

            #Execute user-specified commands after children are resolved
            if not skip:
                cmds = m.post_pull if operation == 'Cloning' else m.post_clone
                for cmd in cmds:
                    out = _exec(shlex.split(cmd), cwd=dst).strip()
                    debug(style(cmd_indenter(out), Style.GRAY, force=True))
            
    #User can optionally specify a single repo to localize
    single_local = False
    if not isinstance(args.local, bool):
        single_local = True
        args.target = args.local[0]
        args.local = True
    if os.path.islink(root.rstrip(os.sep)):
        raise Exception(f"Specified root directory '{root}' is a symlink -- cannot sync ")
    if check_for_state_match(os.path.join(top_root, args.target or ''), filter_type=RepoState.UNALIGNED):
        raise Exception(f"Detected unaligned repos in local (run `gitp status` to see the conflicts`)")
    if standalone and not single_local:
        if args.src:
            debug(f"Pulling {args.src} into {top_root}:\n")
        else:
            debug(f"Pulling {top_root}:\n")

    #Obtain lock before pulling
    m = get_manifest(top_root, create=False)
    w = functools.partial(work, src=args.src, dst=top_root, parent_path=os.path.dirname(top_root), tgt=args.target, level=utility_cmd_level or 0)
    if m and m.lock_server:
        obtain_server_lock(m.lock_server, w)
    else:
        w()
    
    #Apply overlay links at the very end
    if not args.local:
        apply_overlays(top_root, overlay_entries, targets=[args.target] if args.target is not None else [], force=args.force)

PARSERS['pull'].add_argument('src', metavar='src', type=str, nargs='?', help='Repository to pull from')
PARSERS['pull'].add_argument('--target', '-T', dest='target', action='store', default=None, help='Specific repo to synchronize (suffix with / to include descendant repos)')
PARSERS['pull'].add_argument('--local', '-L', dest='local', action='store', nargs='?', const=True, default=False, help='Copy the link sources directly rather than establishing symlinks for linked repos (does not apply to overlays)')
PARSERS['pull'].add_argument('--force', '-F', dest='force', action='store_true', default=False, help='Force destruction of local changes for commit and link-based descendants and allow creation of broken symlinks')


#ANCHOR: clone()
@gitp_operation
def clone(args, unknowns=None):
    '''
    Clone a repository into a new directory, or clone a multirepo view.
    '''
    #A destination path ending in separator means clone inside that directory (inherit src directory name)
    dest_path = os.path.join(args.dst, os.path.basename(args.src)) if args.dst.endswith(os.sep) else args.dst
    if os.path.isdir(dest_path):
        raise Exception(f"Cannot clone {args.src}, target directory {dest_path} already exists")

    #Cloning top-level repo
    debug(f'Cloning {args.src} to {style(dest_path, Style.BOLD)}')
    try:
        _git(f'clone {args_to_str(unknowns)} {args.src} {dest_path}')
    except subprocess.CalledProcessError as e:
        raise Exception(e.output.decode('utf-8').strip()) from None

    #Perform gitp pull on top level to get the rest
    old_argv = sys.argv
    if os.path.isdir(args.src): 
        try: #Only specify src if we know it is another norhelpmal git repo
            if _git('rev-parse --is-bare-repository', args.src).strip() == 'false':
                sys.argv.append(args.src)
        except:
            pass
    sys.argv.append('--force')
    pull(root=dest_path, standalone=False)
    sys.argv = old_argv
    debug(f'\nSuccessfully cloned to {os.path.abspath(dest_path)}.')

PARSERS['clone'].add_argument('src', metavar='src', type=str, help='Parent repo to clone')
PARSERS['clone'].add_argument('dst', metavar='dst', type=str, help='Destination directory')


#ANCHOR: checkout()
@gitp_operation
def checkout(args, unknowns=None):
    '''
    Switch branches or restore working tree files.
    '''
    #Handle changing of repo HEAD (re-sync child repos)
    #Handle reverting local repo state
    global GIT_FALLBACK

    repo_root = get_repo_root()
    m = get_manifest(repo_root)

    def read_manifest_from_ref(ref:str):
        try:
            return _git(f'show {ref}:.gitp_manifest')
        except subprocess.CalledProcessError as e:
            raise Exception(f"The provided ref '{ref}' does not have a .gitp_manifest file -- cannot checkout child repo") from None

    def is_fh(ref:str):
        return os.path.isfile(ref) or os.path.isdir(ref)

    checkout_head = False
    new_branch = args.new_branch or args.orphan

    #Checkout from some ref
    if not is_fh(args.ref[0]) or new_branch:
        src_ref = args.ref[0]
        checkout_head = True
        #If more are specified, invalid cmd; let git fail
        if len(args.ref) > 1:
            GIT_FALLBACK = True
            return
    #Checkout some files from HEAD
    else:
        src_ref = 'HEAD'

    #Collect possible child repos
    ref_files = []
    if '--' in unknowns:
        i = 0
        while unknowns[i] != '--':
            i += 1
        if i < len(unknowns):
            ref_files = unknowns[i+1:]
    if not ref_files: #sole dir(s) specified
        maybe_children = args.ref
    else: #individual dirs w/ some ref specified
        maybe_children = ref_files
        checkout_head = False

    #WORK: Checkout head to point to branch/commit
    if checkout_head:
        #Check for local changes to children and abort if any exist
        if not args.force:
            local_untracked = check_for_changes(repo_root, ignore_committed=True, ignore_untracked=True)
            if local_untracked:
                raise Exception(f'Cannot checkout {src_ref} due to uncommitted changes in the following repos: {", ".join([os.path.relpath(x[0], os.getcwd()) for x in local_untracked])} (push or specify --force to permenantly clobber)')

        #Attempt to perform branch/commit checkout
        try:
            pre_ref_flag = ' -b' if args.new_branch else ( '--orphan' if args.orphan else ('--detach' if args.detach else ''))
            out = _git(f'checkout {pre_ref_flag} {src_ref} {args_to_str(unknowns)}').strip()
            debug(out)
        except subprocess.CalledProcessError as e:
            raise Exception(e.output.decode('utf-8').strip()) from None

        #Determine if this is a branch or a commit
        try:
            _git(f'show-ref --verify refs/tags/{src_ref}')
            is_branch = True
        except:
            try:
                _git(f'show-ref --verify refs/heads/{src_ref}')
                is_branch = True
            except:
                is_branch = False
        
        #Update parent manifest, if possible
        parent_m = get_parent_manifest(repo_root)
        if parent_m:
            entry_name = repo_root.replace(os.path.dirname(parent_m.path) + os.sep, '', 1)
            if is_branch:
                parent_m[entry_name].branch = src_ref
            else:
                parent_m[entry_name].commit = src_ref
            parent_m.write()

        #If not a new branch, re-sync children
        if not new_branch:
            if args.force:
                sys.argv.insert(0, '--force')
            sync()

    #WORK: Checkout specific file(s)
    else:
        
        #Checkout out specific child repo(s)     
        children_to_sync = []
        for child in [x for x in maybe_children]:
            normalized_child = os.path.relpath(os.path.join(repo_root, child.rstrip(os.sep)), os.getcwd())
            if child in m.repos:
                #Read manifest file from ref
                alt_m_content = read_manifest_from_ref(src_ref)
                alt_m = Manifest('alternate', raw=alt_m_content)
                if not args.force:
                    local_untracked = check_for_changes(os.path.join(repo_root, child), ignore_committed=True, ignore_untracked=True)
                    if local_untracked:
                        raise Exception(f'Cannot checkout child repo {normalized_child} due to uncommitted changes (commit or specify --force to permenantly clobber)')
                #Check out child repo by updating the current manifest
                new_child_info = alt_m[child.rstrip(os.sep)]
                m[child.rstrip(os.sep)] = new_child_info
                children_to_sync.append(normalized_child)
                try: sys.argv.remove(child)
                except: pass
                try: ref_files.remove(child)
                except: pass
                maybe_children.remove(child)
            
        #Sync new children states
        old_argv = sys.argv
        if children_to_sync:
            m.write()
            sys.argv = [sys.argv[0]]
            if args.force:
                sys.argv.append('--force')
            sys.argv += children_to_sync
            sync()
        sys.argv = old_argv
    
        #Nothing left to checkout, exit
        if not maybe_children:
            return

        #Passthrough to bare git to check out normal file(s)
        else:
            GIT_FALLBACK = True
            return

PARSERS['checkout'].add_argument('-f', '--force', dest='force', action='store_true', default=False, help='Force operation, discarding local changes in the process')
checkout_type_group = PARSERS['checkout'].add_mutually_exclusive_group()
checkout_type_group.add_argument('--detach', dest='detach', action='store_true', default=False, help='Detaches HEAD at the specified ref')
checkout_type_group.add_argument('--orphan', dest='orphan', action='store_true', default=False, help='Create and checkout a new orphan branch')
checkout_type_group.add_argument('-b', '-B', dest='new_branch', action='store_true', default=False, help='Create and checkout a new branch')
PARSERS['checkout'].add_argument('ref', metavar='ref', nargs='+', type=str, help='Ref to checkout (branch or commit)')


#ANCHOR: mv()
@gitp_operation
def mv(args, unknowns=None):
    '''
    Move or rename a file, a directory, a symlink, or a child repository.
    '''
    #Check for invalid inputs
    root = get_repo_root()
    if root not in os.path.abspath(args.src):
        raise Exception(f"Specified file or directory '{args.src}' is outside of the current repo!")
    if root not in os.path.abspath(args.dst):
        raise Exception(f"Specified destination '{args.dst}' is outside of the current repo!")
    if not args.dst.endswith(os.sep) and (os.path.exists(args.dst) or os.path.islink(args.dst)):
        raise Exception(f"Specified destination '{args.dst}' already exists!")
    if args.dst.endswith(os.sep) and not os.path.isdir(args.dst):
        raise Exception(f"Specified destination directory '{args.dst}' does not exist!")
    if not args.dst.endswith(os.sep) and os.path.dirname(args.dst) and not os.path.isdir(os.path.dirname(args.dst)):
        raise Exception(f"Specified destination path '{os.path.dirname(args.dst)}' does not exist!")

    src_relative_root = get_repo_root(os.path.dirname(args.src))
    dst_relative_root = get_repo_root(os.path.dirname(args.dst))
    try: 
        get_repo_root(args.src)
        src_is_repo = True
    except:
        src_is_repo = False
    src_m = get_manifest(src_relative_root, create=True if src_is_repo else False)
    dst_m = get_manifest(dst_relative_root, create=True if src_is_repo else False)
    src = os.path.relpath(args.src, src_relative_root)
    dst = os.path.relpath(args.dst, dst_relative_root)
    if args.dst.endswith(os.sep):
        if os.getcwd() == os.path.abspath(dst):
            dst = os.path.basename(src)
        else:
            dst = os.path.join(dst, os.path.basename(src))

    #We've moving a repo, invoke special recipe
    if src_m and src in src_m:
        debug(f"Moving repo {style(args.src, Style.BOLD)} to {style(args.dst, Style.BOLD)}")
        #Update the manifest and .gitignore and move it
        gitignore_rm(src_relative_root, src)
        gitignore_add(dst_relative_root, dst)
        shutil.move(args.src, args.dst)
        dst_m.repos[dst] = src_m.repos[src]
        src_m.repos.pop(src)
        src_m.write()
        if src_m != dst_m:
            dst_m.write()
    #We're moving normal files
    else:
        try: 
            #Simple move within repo -- passthrough
            if src_relative_root == dst_relative_root:
                out = _git(f'mv {args_to_str(unknowns)} {src} {dst}', src_relative_root).strip()
                if out:
                    debug(f'> {style(out, Style.GRAY)}')
            #Move of a file across repo boundaries -- do repo bookeeping
            else:
                #Move
                shutil.move(args.src, args.dst)
                try:
                    #Remove from src repo
                    out = _git(f'rm {src}', src_relative_root).strip()
                    if out:
                        debug(f'> {style(out, Style.GRAY)}')
                    #Add to dst repo
                    out = _git(f'add {dst}', dst_relative_root).strip()
                    if out:
                        debug(f'> {style(out, Style.GRAY)}')
                except:
                    shutil.move(args.dst, args.src)
                    raise

        except subprocess.CalledProcessError as e:
            raise Exception(e.output.decode('utf-8').strip())

PARSERS['mv'].add_argument('src', metavar='src', type=str, help='File or directorie to move')
PARSERS['mv'].add_argument('dst', metavar='dst', type=str, help='Destination of file or directory')


#ANCHOR: add()
@gitp_operation
def add(args, unknowns=None):
    '''
    Add file contents to the index.
    '''
    #Check for invalid inputs
    root = get_repo_root()
    for tgt in args.tgt:
        if root not in os.path.abspath(tgt):
            raise Exception(f"Specified file or directory {tgt} is outside of the current repo!")
    #Do work
    for tgt in args.tgt:
        try: 
            relative_root = get_repo_root(os.path.dirname(tgt))
            tgt = os.path.relpath(tgt, relative_root)
            out = _git(f'add {args_to_str(unknowns)} {tgt}', relative_root).strip()
            if out:
                debug(f'> {style(out, Style.GRAY)}')
        except subprocess.CalledProcessError as e:
            raise Exception(e.output.decode('utf-8').strip())
    #If nothing specified, run command recursively on everything (e.g. for git -A, etc)
    if not args.tgt:
        orig_argv = sys.argv
        sys.argv = [sys.argv[0], ('git add ' + args_to_str(unknowns)).strip()]
        exec()
        sys.argv = orig_argv
PARSERS['add'].add_argument('tgt', metavar='tgt', type=str, nargs='*', help='Files or directories to add')


#ANCHOR: rm()
@gitp_operation
def rm(args, unknowns=None):
    '''
    Remove files from the working tree and from the index, or remove child repositories.
    '''
    root = get_repo_root()
    m = get_manifest(root)
    if m is None:
        raise Exception(f"Current directory is not a valid parent repo. Run `gitp sync` to initialize.")
    
    #Check for invalid inputs
    for tgt in args.tgt:
        if root not in os.path.abspath(tgt):
            raise Exception(f"Specified file or directory '{tgt}' is outside of the current repo!")
        new_child = os.path.relpath(tgt, root)

        #Check for overlays first and redirect the user (allowing deletion here would lead to a very confusing repo state)
        if new_child in m and m[new_child].type == 'overlay':
            raise Exception(f"Cannot delete overlayed repo via `gitp rm` -- use `gitp unlink {tgt} --overlay` instead")

    #Start removal
    for tgt in args.tgt:
        new_child = os.path.relpath(tgt, root)
        #Handle case wherein a child repo is deleted from a grandparent or greater
        starting = os.path.join(tgt, '')
        tgt_part = os.path.relpath(os.path.split(starting)[0], root) #handle absolute paths
        while tgt_part != '':
            child_m = get_manifest(os.path.join(root, tgt_part), create=False)
            if child_m is not None:
                child_root = os.path.join(root, tgt_part)
                new_tgt = os.path.relpath(tgt, child_root)
                #TODO: handle trailing / slashes (with or without)
                if new_tgt in child_m:
                    root = child_root
                    new_child = new_tgt
                    m = child_m
                    break
            tgt_part = os.path.split(tgt_part)[0]

        #Don't modify contents accessed via links
        if os.path.islink(root):
            raise Exception(f"Attempted to delete a child repo of a linked repo {root} -- run `gitp pull {root} --local` to convert this into a physical repo before modifying it")

        #Passthrough for normal git files
        if new_child not in m:
            try: 
                relative_root = get_repo_root(os.path.dirname(tgt))
                tgt = os.path.relpath(tgt, relative_root)
                out = _git(f'rm {args_to_str(unknowns)} {tgt}', relative_root).strip()
                debug(f'> {style(out, Style.GRAY)}')
            except subprocess.CalledProcessError as e:
                raise Exception(e.output.decode('utf-8').strip())
                
        #Remove child repo
        else:
            m.pop(new_child)

            #Check if there are any local changes in the target or any decendents of the target repo
            remove_dir = False
            if os.path.isdir(tgt):
                if args.force:
                    remove_dir = True
                else:
                    locally_modified_repos = check_for_changes(tgt)
                    locally_modified_repos = [x[0] for x in locally_modified_repos]
                    if not locally_modified_repos:
                        remove_dir = True
                    else:
                        raise Exception(f"The following decendent repos have local changes!"+'\n  '+'\n  '.join(locally_modified_repos)+"\nPush or revert the changes prior to deleting, or specify --force to force deletion.")

            #Update manifest and .gitignore 
            m.write()
            gitignore_rm(root, new_child)
                
            #Remove the directory
            if remove_dir == True:
                debug(f"Removing {style(tgt, Style.BOLD)}", level=1)
                if is_real_dir(tgt):
                    shutil.rmtree(tgt)
                elif os.path.islink(tgt):
                    os.unlink(tgt)
            debug(style(f"Deleted child repo {style(tgt, Style.BOLD)}", []))

PARSERS['rm'].add_argument('tgt', metavar='tgt', type=str, nargs='+', help='Files or directories to remove')
PARSERS['rm'].add_argument('--force', '-f', dest='force', action='store_true', required=False, default=None, help='Force deletion even if there are local changes present')


#ANCHOR: status()
@gitp_operation
def status(args, unknowns=None, print_msgs:bool=True):
    '''
    Print out all child repo information from .gitp_manifest.
    '''
    msg_level = 0 if print_msgs else 1
    format_key = {
        RepoState.CLEAN:        style('✓', Style.GREEN),
        RepoState.MODIFIED:     style('*', Style.YELLOW),
        RepoState.NONEXISTENT:  style('-', Style.GRAY),
        RepoState.UNALIGNED:    style('!', Style.RED),
        RepoState.UNLINKED:     style('#', Style.CYAN),
        RepoState.OVERLAYED:    style('^', Style.BLUE),
    }
    status_cnt = {x:0 for x in format_key}

    def work(root:str, level:int=0, name:typing.Union[str,None]=None, md:typing.Union[Manifest.Repo,None]=None, mismatches:dict=None):
        nonlocal status_cnt
        nonlocal format_key
        nonlocal repo_root
        nonlocal overlays
        try: 
            m = get_manifest(root, create=False)
        except:
            if level:
                raise
            #Outstanding merge -- fallback on plain git command
            if merge_in_progress(repo_root):
                debug(style(f'Error: Unable to parse .gitp_manifest file due to outstanding git merge conflict:', Style.RED))
                global GIT_FALLBACK
                GIT_FALLBACK = True
                return

        indent = ''.join([' ' for _ in range(level)]) + '∟' if level else ''
        cmd_indenter = get_cmd_indenter(1 if not level else level + 2)
        we_are_a_link = os.path.islink(root) and md and md.link

        status = RepoState.CLEAN
        changes = None
        exp_overlay_link = None
        if root in overlays: #check that the overlay link is present and aligned
            status, curr_link, exp_overlay_link = check_for_overlay_state_match(root, overlays, repo_root)
        else:
            #check for repo state alignment
            if mismatches and name in mismatches:
                curr_branch,curr_commit,curr_link,status = mismatches[name]
            #check for local changes
            elif not we_are_a_link: #don't capture local changes for linked repos (less noisy)
                changes = check_for_changes(root, recurse=False, ignore_local_branches=True) 
                if changes:
                    status = RepoState.MODIFIED
                    changes = changes[0][1]
        status_cnt[status] += 1
        url_info = ''
        if md:
            url     = md.url
            branch  = md.branch
            link    = md.link if status in [RepoState.CLEAN, RepoState.UNALIGNED] else ''
            commit  = md.commit or ''
            if len(commit) > 8:
                commit = commit[0:8]
            if status == RepoState.OVERLAYED:
                link = curr_link
                commit = ''
            if exp_overlay_link:
                link = exp_overlay_link
            alert_str = ''
            ref_type = 'link:' if link else 'SHA:' if commit else ''
            if status == RepoState.UNALIGNED:
                alert_str = f'!={curr_commit[0:8]}' if not link and commit else f'!={curr_link or curr_branch}'
            inner_string = (link or commit or branch) + alert_str
            if alert_str: inner_string = style(inner_string, Style.RED)
            url_info = f'{url} ({ref_type}{inner_string})'
        else:
            try:
                curr_branch = _git('symbolic-ref -q HEAD', root).rsplit('/', 1)[-1].strip()
            except:
                curr_branch = ''
                curr_commit = _git('rev-parse HEAD', root).strip()
            url_info = f"{', '.join([x['fetch'] for x in get_remotes(root).values()])} ({curr_branch or curr_commit})"
        repo_print_name = os.path.relpath(root, os.getcwd()) if level else root
        symbol = style(changes, Style.YELLOW) if changes else format_key[status]
        debug(f'{indent}[{symbol}] {style(repo_print_name, Style.BOLD)} {url_info}', level=msg_level)
        if not args.short and not we_are_a_link and os.path.exists(root):
            out = _git(f'status {args_to_str(unknowns)}', root).strip()
            out = abbreviate_status_print(out)
            if out:
                debug(cmd_indenter(out))

        #Recurse children
        if m:
            mismatches = check_for_state_match(root, recurse=False)
            for child,child_info in m.items():
                child_root = os.path.join(root, child)
                #Don't render overlays as independent, top-level entries to avoid confusion
                if root == repo_root and child_root in overlays:
                    continue
                work(child_root, level=level+(1 if level==0 else 2), name=child, md=child_info, mismatches=mismatches)

    repo_root = get_repo_root()
    m = get_manifest(repo_root)
    overlays=m.get_overlays()
    work(repo_root)
    if GIT_FALLBACK:
        return 1
    warn_footer = ''
    footer = ''
    if status_cnt[RepoState.MODIFIED]:
        warn_footer += '\n{' + format_key[RepoState.MODIFIED] + '} You have repo(s) with local, unpushed changes'
    if status_cnt[RepoState.UNALIGNED]:
        warn_footer += '\n{' + format_key[RepoState.UNALIGNED] + '} You have repo(s) that are out of sync with .gitp_manifest (run `gitp sync` to automatically update .gitp_manifest recursively)'
    if status_cnt[RepoState.NONEXISTENT]:
        warn_footer += '\n{' + format_key[RepoState.NONEXISTENT] + '} You have repo(s) that are not cloned locally (run `gitp sync` to clone them)'
    if status_cnt[RepoState.UNLINKED]:
        warn_footer += '\n{' + format_key[RepoState.UNLINKED] + '} You have repo(s) that are copied locally from a link (run `gitp pull` to relink them)'
    if status_cnt[RepoState.OVERLAYED]:
        footer += '\n{' + format_key[RepoState.OVERLAYED] + '} You have repo(s) that are locally overlayed'
    if not warn_footer:
        footer += '\nAll child repos in sync!'
    debug(footer + warn_footer, level=msg_level)
    if warn_footer:
        return 1
    return 0

PARSERS['status'].add_argument('--short', '-s', dest='short', action='store_true', required=False, default=None, help='Disable showing individual file status details for each repo')


#ANCHOR: push()
@gitp_operation
def push(args, unknowns=None):
    '''
    Attempt to push this repo. Checks that .gitp_manifest matches the current repo state.
    '''
    #Recursively push all repos in reverse order
    def work(root, name:typing.Union[str,None]=None, url:typing.Union[str,None]=None, branch:typing.Union[str,None]=None, level:int=0):
        repo_print_name = os.path.relpath(root, os.getcwd()) if level else root
        indent = ''.join([' ' for _ in range(level)]) if level else ''
        cmd_indenter = get_cmd_indenter(level + 2)
        if level: indent += '┌'
        #Don't ever push linked repos
        assert(not root.endswith(os.sep)) #FIXME REMOVE SANITY CHECK
        if os.path.islink(root):
            debug(f' {indent} {style(repo_print_name, Style.BOLD)} ' + style('skipping linked repo', Style.GRAY))
            return
        m = get_manifest(root, create=False)
        if m:
            for child,child_info in m.items():
                child_root = os.path.join(root, child)
                work(child_root, name=child, url=child_info.url, branch=child_info.branch, level=level+2)
        if check_for_changes(root, recurse=False, ignore_uncommitted=True):
            url_info = f'{url} ({branch})'
            debug(f' {indent} Pushing {style(repo_print_name, Style.BOLD)} to remote {style(url_info, Style.BLUE)}')
            try:
                out = _git(f'push {args_to_str(unknowns)}', root).strip()
                debug(style(cmd_indenter(out), Style.GRAY, force=True))
            except subprocess.CalledProcessError as e:
                raise Exception(f"Failed to push {root} due to the following reason(s):\n{e.output.decode('utf-8').strip()}")
        else:
            debug(f'{indent} {style(repo_print_name, Style.BOLD)} ' + style('nothing to push', Style.GRAY))

    repo_root = get_repo_root()
    if not args.force:
        unclean = check_for_state_match(repo_root, filter_type=RepoState.UNALIGNED)
        if unclean:
            #Filter out overlays from state match result as they have different state match criteria
            m = get_manifest(repo_root, create=False)
            overlays = {x:y for x,y in m.items() if y.type == 'overlay'}
            for repo in [x for x in unclean if x in overlays]:
                if check_for_overlay_state_match(repo, overlays, repo_root)[0] == RepoState.OVERLAYED:
                    unclean.pop(repo)
            if unclean:
                raise Exception(f"Cannot push due to unclean child repo state(s): {', '.join(unclean.keys())}. Run `gitp status` to see the offending repos, or run with --force to ignore.")
            
    debug(f'Pushing {repo_root}:')
    #Get url and branch info of top-level repo for print messages
    idx = None
    for i,x in enumerate(unknowns):
        if not x.startswith('-'):
            idx = i
            break
    if idx is not None:
        url = unknowns[idx]
    else:
        url = get_remotes(repo_root)['origin']['push']
    branch = get_current_branch(repo_root)
    
    #Obtain lock for top-level repo if a lock server is specified
    m = get_manifest(repo_root, create=False)
    w = functools.partial(work, repo_root, name=os.path.basename(os.getcwd()), url=url, branch=branch)
    if m and m.lock_server:
        obtain_server_lock(m.lock_server, w)
    else:
        w()
    debug(f'\nPush completed.')

PARSERS['push'].add_argument('--force', '-f', dest='force', action='store_true', required=False, default=None, help='Force push to remote even if child repo state is not clean')


#ANCHOR: exec()
@gitp_operation
def exec(args, unknowns=None, stop_on_error:bool=False, interactive:bool=False, force_color:bool=False):
    '''
    Execute arbitrary commands on child repos.
    '''
    args.tgt = [x for y in args.tgt for x in y]
    args.filter = [x for y in args.filter for x in y]
    args.cmd = [x for x in args.cmd if x]
    if not args.cmd:
        raise Exception(f"No commands specified")
    if not args.filter and not args.tgt:
        args.filter = ['.*']
    command_targets = []
    regexes = []
    for regex in args.filter:
        try: regexes.append(re.compile(regex))
        except Exception as e: 
            raise Exception(f"Invalid regular expression '{regex}' specified: {e} ") from None

    def work(root:str, level:int=0):
        added = False
        skip_reason = False
        for t in [x for x in args.tgt]:
            if os.path.abspath(t) == os.path.abspath(root):
                args.tgt.remove(t)
                added = True
                break
        if not added:
            for regex in regexes:
                if re.search(regex, root):
                    added = True
                    break
        if args.modified:
            added = True if check_for_changes(root, recurse=False) else False
        if added:
            #Skip execution on nonexistent repos
            if not os.path.exists(root):
                skip_reason = 'not locally present'
            #Skip execution on linked repos
            elif os.path.islink(root):
                skip_reason = 'linked to ' + os.readlink(root)
            command_targets.append((level, root, skip_reason))
        if not skip_reason:
            m = get_manifest(root, create=False)
            if not m:
                return
            for child in m:
                work(os.path.join(root, child), level=level+2)
        
    repo_root = get_repo_root()
    work(repo_root)

    if args.tgt:
        raise Exception(f'Unable to resolve the following specified target repos: {args.tgt}')

    if args.preview:
        debug(style(f"Command execution preview for: {args.cmd}", []))
    else:
        debug(style(f"Executing commands: {args.cmd}", []))
    for cmd in args.cmd:
        if not command_targets and not args.preview:
            raise Exception(f"No matching repos for regular expression '{args.filter}'")
        for level,dir,skip_reason in command_targets:
            indent = ''.join([' ' for _ in range(level-1)]) if level else ''
            top_indent = indent + '∟' if level else ''
            bottom_indent = indent + ' ' if level else indent
            cmd_indenter = get_cmd_indenter(1 if not level else level + 1)
            relpath = os.path.relpath(dir, os.getcwd())
            if relpath == '.':
                relpath = os.path.abspath(dir)

            if skip_reason:
                debug(style(f"{top_indent}[$] {relpath} : skipping ({skip_reason})", [Style.BOLD]))
                debug(style(f'{bottom_indent}[?] {dir}', [Style.YELLOW]))
                continue
            debug(style(f"{top_indent}[$] {relpath} : executing '{cmd}'", Style.BOLD))
            if not args.preview:
                try:
                    if cmd.startswith('git '):
                        out = _git(cmd[4:], cwd=dir, interactive=interactive)
                    else: 
                        out = _exec(shlex.split(cmd), cwd=dir, interactive=interactive)
                    if not interactive and out.strip():
                        out = cmd_indenter(out)
                        if force_color:
                            out = style(out, Style.GRAY, force=True)
                        debug(out)
                    failed = False
                except subprocess.CalledProcessError as e:
                    error(cmd_indenter(e.output.decode('utf-8')))
                    failed = True
                except FileNotFoundError as e:
                    error(cmd_indenter(str(e)))
                    failed = True
                if failed:
                    if stop_on_error:
                        raise Exception(f"Failed executing '{cmd}'")
                    error(f'{bottom_indent}[x] {dir}')
                else:
                    debug(style(f'{bottom_indent}[✓] {dir}', [Style.GREEN]))

PARSERS['exec'].add_argument('--target', '-t', dest='tgt', type=str, action='append', nargs='+', required=False, default=[], help='Specific target descendent repo(s) to execute command(s) on')
PARSERS['exec'].add_argument('--filter', '-x', dest='filter', type=str, action='append', nargs='+', required=False, default=[], help='Regex expressions to filter out which descendent repo to execute command(s) on (does not affect --target)')
PARSERS['exec'].add_argument('--modified', '-m', dest='modified', action='store_true', required=False, default=None, help='Only run command on repos that have local changes')
PARSERS['exec'].add_argument('--links', '-l', dest='links', action='store_true', required=False, default=None, help='Enable running of commands on linked repos')
PARSERS['exec'].add_argument('--preview', '-p', dest='preview', action='store_true', required=False, default=False, help='Preview all commands to be executed with these command(s)')
PARSERS['exec'].add_argument('cmd', metavar='cmd', type=str, nargs='+', help='List of commands to execute on descendent repo(s)')
PARSERS['exec']._preprocess_method = quote_exec_cmd


#ANCHOR: sync()
@gitp_operation
def sync(args, unknowns=None):
    '''
    Synchronize a repo's state with its manifest.
    '''
    
    def do_sync(tgts:typing.List[str], overlays:dict=None, mismatches:dict=None, top_root:str=None, level:int=0):
        indent      = ''.join([' ' for _ in range(level)]) + '∟ ' if level else ''
        cmd_indenter = get_cmd_indenter(level + 2)

        old_argv = sys.argv
        #Sync each target (don't touch linked repos that are cloned locally)
        for tgt in tgts:
            parent_m = get_parent_manifest(tgt)
            m = get_manifest(tgt, create=False)
            repo_root = os.path.dirname(parent_m.path)
            child = os.path.abspath(tgt).replace(repo_root + os.sep, '', 1)
            child_qualified = os.path.abspath(tgt).replace(top_root + os.sep, '', 1)
            #Sync only if it is unaligned (and not overridden w/ an overlay)
            if child_qualified in mismatches and os.path.abspath(child_qualified) not in overlays:
                state = mismatches[child_qualified][-1]
                #Child doesn't exist -- pull it
                if state == RepoState.NONEXISTENT and not parent_m[child].link:
                    debug(f'{indent}Syncing {style(tgt, Style.BOLD)} (cloning)')
                    sys.argv = [old_argv[0], '--target', child]
                    pull(root=repo_root, standalone=False, utility_cmd_level=level)
                    sys.argv = old_argv
                #Child exists but isn't aligned; align it
                elif state == RepoState.UNALIGNED or parent_m[child].link:
                    #Relink
                    if parent_m[child].link:
                        link = resolve_repo_link(top_root if parent_m[child].type == 'overlay' else repo_root, parent_m[child], fail=not args.force)
                        if not os.path.isdir(link) and not args.force:
                            raise Exception(f"Specified link {parent_m[child].link} for {child_qualified} does not exist (specify with --force to ignore)")
                        if not os.path.isabs(parent_m[child].link):
                            link = os.path.relpath(link, repo_root)
                        if os.path.islink(tgt):
                            os.unlink(tgt)
                        if is_real_dir(tgt):
                            debug(f'{indent}Syncing {style(tgt, Style.BOLD)} (skipping locally copied repo)')
                        else:
                            debug(f'{indent}Syncing {style(tgt, Style.BOLD)} (linking to {link})')
                            os.symlink(link, os.path.join(repo_root, child))
                    #Fetch and checkout
                    else:
                        debug(f'{indent}Syncing {style(tgt, Style.BOLD)} (checking out {parent_m[child].commit or parent_m[child].branch})')
                        try:
                            out = _git('fetch', tgt).strip()
                            out = _git(f'checkout {parent_m[child].commit or parent_m[child].branch}', tgt).strip()
                            debug(style(cmd_indenter(out), Style.GRAY, force=True))
                        except subprocess.CalledProcessError as e:
                            raise Exception(f"Failed to add child repo {args.dst} for the following reason(s):\n{e.output.decode('utf-8').strip()}")
                else:
                    debug(f'{indent}Syncing {style(tgt, Style.BOLD)}')
            else:
                debug(f'{indent}Syncing {style(tgt, Style.BOLD)}')

            #Recursively sync children
            if m:
                for gchild in m:
                    do_sync([os.path.join(tgt, gchild)], overlays=overlays, mismatches=mismatches, top_root=top_root, level=level+2)

    #If nothing is specified, target all children of repo in cwd
    repo_root = get_repo_root()
    m = get_manifest(repo_root)
    mismatches = check_for_state_match(repo_root, recurse=True)
    if not args.tgt:
        args.tgt = [x for x in m]

    for tgt in args.tgt:
        child = os.path.abspath(tgt).replace(repo_root + os.sep, '', 1)
        #Check that all specified targets fall within the current repo boundary
        if not os.path.abspath(tgt).startswith(repo_root):
            raise Exception(f'Specified sync target {tgt} falls outside of the current repo hierarchy')
        #Error out if local changes are detected in any target repos (only run at top once since it is recursive)
        mismatched_repos = [x for x,md in mismatches.items() if x.startswith(child) and md[-1] == RepoState.UNALIGNED]
        if not args.force and check_for_changes(tgt, ignore_committed=True, ignore_untracked=True) and mismatched_repos:
            raise Exception(f'Cannot sync repo {tgt} due to untracked changes (commit or specify --force to permenantly clobber)')

    #Sync recursively
    overlays = m.get_overlays()
    if isinstance(args.tgt, str):
        args.tgt = [args.tgt]
    do_sync(args.tgt, overlays=overlays, mismatches=mismatches, top_root=repo_root)

    #Apply overlays
    apply_overlays(repo_root, overlays, args.tgt, args.force)

PARSERS['sync'].add_argument('-f', '--force', dest='force', action='store_true', default=False, help='Force operation, discarding local changes in the process')
PARSERS['sync'].add_argument('tgt', metavar='tgt', nargs='*', type=str, help='Child repos to synchronize (specify none to synchronize all children)')


#ANCHOR: new()
@gitp_operation
def new(args, unknowns=None):
    '''
    Add a new child repo.
    '''        
    if not args.link and not args.branch and not args.commit:
        raise Exception(f"You must specify exactly one of --link, --branch, --commit")

    root = get_repo_root()
    m = get_manifest(root)
    
    #Check for invalid inputs
    args.dst = args.dst[0]
    if args.dst in m:
        raise Exception(f"Child repo '{args.dst}' already exists! Remove it before overwritting it.")
    if os.path.isdir(args.dst):
        raise Exception(f"Specified destination directory '{args.dst}' already exists!")
    if os.path.dirname(args.dst) and not os.path.isdir(os.path.dirname(args.dst)):
        raise Exception(f"Specified destination path '{os.path.dirname(args.dst)}' doesn't exit!")
    if root not in os.path.abspath(args.dst):
        raise Exception(f"Specified destination directory '{args.dst}' is outside of the current repo!")
    if args.url.startswith('.'):
        raise Exception(f"Invalid url '{args.url}' specified -- cannot be a relative path.")
    new_child = os.path.relpath(args.dst, root)

    #First time -- init the repo
    if m is None:
        pull()
    m = get_manifest(root)
    
    #Handle case wherein a child repo is added from a grandparent or greater
    starting = os.path.join(args.dst, '')
    dst_part = os.path.relpath(os.path.split(starting)[0], root) #handle absolute paths
    while dst_part != '':
        #This is a child repo -- make sure that we create a manifest for it if it doesn't exist
        create = True if dst_part in m else False
        child_m = get_manifest(os.path.join(root, dst_part), create=create)
        if child_m is not None:
            #Check if any entries that fall under dst exist at this level, and if so, error out
            conflicting_children = [x for x in child_m if x.startswith(os.path.dirname(dst_part))]
            if conflicting_children:
                raise Exception(f"Child repo {dst_part} contains children {', '.join(conflicting_children)} which conflict with adding new child '{args.dst}' (please manually reassign these repos to preserve hiearchy)") #FIXME: handle this for user via interactive prompt?
            root = os.path.join(root, dst_part)
            new_child = os.path.relpath(args.dst, root)
            m = child_m
            break
        dst_part = os.path.split(dst_part)[0]

    #Don't modify contents accessed via links
    assert(not root.endswith(os.sep)) #FIXME REMOVE SANITY CHECK
    if os.path.islink(root):
        raise Exception(f"Attempted to add a child repo to a linked repo {root} -- run `gitp pull {root} --local` to convert this into a physical repo before modifying it")

    #Register the new child repo by updating the manifest and .gitignore
    new_entry = Manifest.Repo()
    new_entry.commit        = args.commit
    new_entry.branch        = args.branch
    new_entry.link          = args.link
    new_entry.link_filter   = args.link_filter
    new_entry.link_newest   = args.link_newest
    new_entry.url           = args.url
    m[new_child] = new_entry
    m.write()
    gitignore_add(root, new_child)

    #Clone the new repo recursively, back out change if something fails
    try: 
        sys.argv = [sys.argv[0], '--target', new_child + os.sep]
        pull(root=root, standalone=False)
    except subprocess.CalledProcessError as e:
        sys.argv.append(args.dst)
        if args.force:
            sys.argv.append('--force') 
        rm()
        raise Exception(f"Failed to add child repo '{args.dst}' for the following reason(s):\n{e.output.decode('utf-8')}")
    debug(style(f"Added child repo {style(args.dst, Style.BOLD)}", []))

PARSERS['new'].add_argument('dst', metavar='dst', type=str, nargs=1, help='Destination directory of new child repo')
PARSERS['new'].add_argument('--from', '-r', dest='url', action='store', required=True, default=None, help='Remote URL to clone child repo from')
new_type_group = PARSERS['new'].add_mutually_exclusive_group()
new_type_group.add_argument('--branch', '-b', dest='branch', action='store', default='master', help='Branch or tag to clone')
new_type_group.add_argument('--commit', '-c', dest='commit', action='store', default=None, help='Commit SHA to clone')
new_type_group.add_argument('--link', '-l', dest='link', action='store', default=None, help='Path to (sym)link to in place of cloning from a remote repo, or a directory of linkable targets if --newest is specified')
PARSERS['new'].add_argument('--newest', '-N', dest='link_newest', action='store_true', default=None, help='Link the most recently created directory found in the directory specified by --link')
PARSERS['new'].add_argument('--link_filter', '-L', dest='link_filter', action='store', default=None, help='Filter to apply to directories evaluated via --newest')
PARSERS['new'].add_argument('--force', '-f', dest='force', action='store_true', default=False, help='Force creation of broken links')


#ANCHOR: link()
@gitp_operation
def link(args, unknowns=None):
    '''
    Replace a child repo with a symlink.
    '''
    args.tgt = args.tgt.rstrip(os.sep)
    args.link = args.link.rstrip(os.sep)

    if args.link_filter and not args.newest:
        raise Exception(f"Must specify --newest with --filter")

    regexes = []
    if args.link_filter:
        for regex in args.link_filter:
            try: regexes.append(re.compile(regex))
            except Exception as e: 
                raise Exception(f"Invalid regular expression '{regex}' specified: {e} ") from None
    repo_root = get_repo_root()

    root = get_repo_root()
    src_parent_root = get_repo_root(os.path.dirname(args.tgt))
    child_root = os.path.abspath(args.tgt)
    child = os.path.relpath(args.tgt, src_parent_root)
    overlay_name = os.path.relpath(args.tgt, root)
    m = get_manifest(src_parent_root)

    if not src_parent_root.startswith(root):
        raise Exception(f"Specified path '{args.tgt}' falls outside of the current repo hierarchy")

    if not m or child not in m:
        raise Exception(f"Unknown repo '{child}' specified (add repos via `gitp new` first)")

    #Check that the link src directory is valid
    if not args.force:
        rel_link_boundary = src_parent_root if not args.link_overlay else repo_root
        if not os.path.isdir(args.link):
            raise Exception(f"Cannot update {child} to link to non-existent directory '{args.link}' (run with --force to ignore)")
        elif not os.path.isabs(args.link) and not os.path.abspath(args.link).startswith(rel_link_boundary):
            raise Exception(f"Cannot update {child} to link to an externally pointing relative path '{args.link}' (run with --force to ignore)")
    if os.path.abspath(args.link) == child_root:
        raise Exception(f"Cannot set {child} to link to itself")

    #Check for local changes of link target to avoid squashing them
    if not args.force and is_real_dir(child_root):
        if check_for_changes(child_root):
            raise Exception(f"Local changes detected within {args.tgt} (specify --force to permenantly clobber)")
        if _git('stash list', child_root):
            raise Exception(f"Local stashed changes detected within {args.tgt} (specify --force to permenantly clobber)")

    #We create a new entry for overlay
    if args.link_overlay:
        root_m = get_manifest(root)
        root_m[overlay_name] = Manifest.Repo()
        root_m[overlay_name].type = 'overlay'
        root_m[overlay_name].link = args.link
        root_m[overlay_name].link_newest = args.link_newest
        root_m[overlay_name].link_filter = args.link_filter
        root_m.write()
    #Convert or update link of existing entry
    else:
        m[child].type = 'repo'
        m[child].link = os.path.relpath(args.link, src_parent_root) if not os.path.isabs(args.link) else args.link
        m[child].commit = None #leaving this set is confusing -- nuke it
        m[child].link_newest = args.link_newest
        m[child].link_filter = args.link_filter
        m.write()
    
    #Replace any existing content w/ the new link
    if is_real_dir(args.tgt):
        shutil.rmtree(args.tgt)
    elif os.path.islink(args.tgt):
        os.unlink(args.tgt)
    old_argv = sys.argv
    sys.argv = [sys.argv[0], args.tgt]
    sync()
    sys.argv = old_argv

    sfx = '' if not args.link_newest else 'last modified subdir'
    if args.link_filter:
        sfx += f"that matches regex '{args.link_filter}'"
    if sfx:
        sfx = f' ({sfx})'
    if args.link_overlay:
        debug(f"Applied overlay on {args.tgt} to {args.link}{sfx}")
    else:
        debug(f"Linked {args.tgt} to {args.link}{sfx}")

PARSERS['link'].add_argument('tgt', metavar='tgt', type=str, action='store', default=None, help='Target repo to convert into link')
PARSERS['link'].add_argument('link', metavar='link', type=str, action='store', default=None, help='Link source')
PARSERS['link'].add_argument('--newest', '-N', dest='link_newest', action='store_true', default=None, help='Link the most recently created directory found in the directory specified by --link')
PARSERS['link'].add_argument('--filter', '-L', dest='link_filter', action='store', default=None, help='Regex filter to apply to directories evaluated via --newest')
PARSERS['link'].add_argument('--force', '-f', dest='force', action='store_true', default=False, help='Force update regardless of the existence of link source')
PARSERS['link'].add_argument('--overlay', '-O', dest='link_overlay', action='store_true', default=False, help='Apply link as an overlay (no change to child repo manifest)')


#ANCHOR: unlink()
@gitp_operation
def unlink(args, unknowns=None):
    '''
    Replace a linked child repo with the child repo itself.
    '''
    args.tgt = args.tgt.rstrip(os.sep)

    root = get_repo_root()
    src_parent_root = get_repo_root(os.path.dirname(args.tgt))
    if not src_parent_root.startswith(root):
        raise Exception(f"Specified path '{args.tgt}' falls outside of the current repo hierarchy")

    #Update manifest
    if args.link_overlay:
        m = get_manifest(root)
        overlay_name = os.path.relpath(args.tgt, root)
        if overlay_name not in m:
            raise Exception(f"Overlay '{args.tgt}' does not exist")
        m.pop(overlay_name)
        m.write()
    else:
        child = os.path.relpath(args.tgt, src_parent_root)
        m = get_manifest(src_parent_root)
        if child not in m:
            raise Exception(f"Repo '{args.tgt}' does not exist")
        if m[child].link is None:
            raise Exception(f"Repo '{args.tgt}' is not linked, nothing to unlink")
        m[child].link = None
        m.write()

    #If link exists, replace it with cloned copy (or another link if we are remove an overlay from a linked repo)
    if os.path.islink(args.tgt):
        os.unlink(args.tgt)
        old_argv = sys.argv
        sys.argv = [sys.argv[0], args.tgt]
        sync()
        sys.argv = old_argv

    if args.link_overlay:
        debug(f"Removed overlay from {args.tgt}")
    else:
        debug(f"Unlinked {args.tgt}")

PARSERS['unlink'].add_argument('tgt', metavar='tgt', type=str, action='store', default=None, help='Target repo to remove link for')
PARSERS['unlink'].add_argument('--overlay', '-O', dest='link_overlay', action='store_true', default=False, help='Remove an overlay (no change to child repo manifest)')


#ANCHOR: server()
@gitp_operation
def server(args, unknowns=None):
    '''
    Run a gitp lock server to enable atomic fetch and push operations on some set of repos.
    '''
    import asyncio, random
    async def run_server(host:str, port:int, max_conns:int=-1, timeout:int=-1, timeout_margin:int=5):
        IDS         = []
        EVENTS      = []
        EVENTS_LOCK = asyncio.Semaphore(1)
        MAX_CONNS   = max_conns
        TIMEOUT     = timeout
        CLIENT_TIMEOUT = timeout - timeout_margin

        def get_req_id():
            id = random.randrange(1, 65536)
            while id in IDS:
                id = random.randrange(1, 65536)
            IDS.append(id)
            return id

        async def handle_lock_req(reader, writer):
            '''
            After an inbound http request has been decoded as a lock acquisition request, return either:

                1. the lock, with a suggested client-side timeout duration 
                2. the client's place in line for lock acquisition
            
            The TCP connection is maintained until the client terminates the TCP connection (forfeits its lock or forfeits its place in line for the lock). While a client is waiting for the lock, the server sends a notification to the client every time their place in line has changed. If the server terminates the TCP connection, clients are to assume failure to acquire the lock and exit appropriately.
            '''
            #Reject connection -- queue is full
            if len(EVENTS) >= MAX_CONNS:
                writer.close()
                return
            
            #Place client in line
            obtained_lock_event = asyncio.Event()
            await EVENTS_LOCK.acquire()
            EVENTS.append(obtained_lock_event)
            EVENTS_LOCK.release()
            lock_granted = False
            try:
                #Assign the client request a unique ID
                req_id = get_req_id()
                debug(f"Received new lock request from client#{style(req_id, Style.BOLD)}")
                writer.write(f"{req_id}.".encode('utf-8'))
                await writer.drain()

                #Notify client of a change in their place in line
                async def notify_change():
                    while True:
                        place_in_line = 0
                        for i, x in enumerate(EVENTS):
                            if x is obtained_lock_event:
                                place_in_line = i
                        #Send suggested clientside timeout duration
                        if place_in_line == 0:
                            nonlocal lock_granted
                            lock_granted = True
                            debug(f"Client#{style(req_id, Style.BOLD)} obtained lock")
                            writer.write(f"{place_in_line}:{CLIENT_TIMEOUT}".encode('utf-8'))
                            break
                        else:
                            # debug(f"SERVER: {place_in_line}")
                            writer.write(f"{place_in_line}.".encode('utf-8'))
                        await obtained_lock_event.wait()
                        obtained_lock_event.clear()
                await notify_change()
                
                #Lock obtained -- wait for client to return the lock 
                async def wait_for_release():
                    while True: #do nothing until timeout or client disconnects
                        rsp = await reader.read(255)
                        if rsp:
                            raise Exception("(connection closed)")
                        await asyncio.sleep(0.2)
                await asyncio.wait_for(wait_for_release(), TIMEOUT)
            except KeyboardInterrupt:
                debug("Killing server")
                writer.close()
            except TimeoutError as e:
                debug(f"Client#{style(req_id, Style.BOLD)} timed out -- auto-releasing the lock")
                writer.close()
            except:
                if lock_granted:
                    debug(f"Client#{style(req_id, Style.BOLD)} closed the connection, forfeiting their lock")
                else:
                    debug(f"Client#{style(req_id, Style.BOLD)} closed the connection, forfeiting their place in line")
            finally:
                IDS.remove(req_id)
                writer.close()
                await EVENTS_LOCK.acquire()
                if obtained_lock_event in EVENTS:
                    EVENTS.remove(obtained_lock_event)
                for e in EVENTS: #notify peers that event queue state changed
                    e.set()
                EVENTS_LOCK.release()

        server = await asyncio.start_server(handle_lock_req, host, port)
        async with server:
            await server.serve_forever()

    debug(f"Starting gitp lock server on {args.host}:{args.port} (queue size of {args.queue_size})")
    debug(f"Use this lock server by adding 'lock_server: {args.host}:{args.port}' to the .gitp_manifest file of the repos you wish to serialize fetch and push operations for.")
    if args.timeout_margin >= args.timeout:
        raise Exception(f"Please specify a --timeout_margin greater than --timeout")
    try:
        asyncio.run(run_server(args.host, args.port, max_conns=args.queue_size, timeout=args.timeout, timeout_margin=args.timeout_margin))
    except KeyboardInterrupt:
        debug(f"\nLock server disconnected due to CTRL+C")

PARSERS['server'].add_argument('--queue_size', '-s',     dest='queue_size',      type=int, action='store', default=10,              help='Maximum number of clients that can wait to acquire the lock')
PARSERS['server'].add_argument('--host', '-H',           dest='host',            type=str, action='store', default='localhost',     help='Hostname of server')
PARSERS['server'].add_argument('--port', '-p',           dest='port',            type=int, action='store', default=5555,            help='TCP port to listen for connections on')
PARSERS['server'].add_argument('--timeout', '-t',        dest='timeout',         type=int, action='store', default=30,              help='Number of seconds before an acquired lock is automatically released from the client')
PARSERS['server'].add_argument('--timeout_margin', '-T', dest='timeout_margin',  type=int, action='store', default=5,               help='Number of seconds subtracted from --timeout for the suggested client-side timeout')


#ANCHOR: help()
@gitp_operation
def help(args, unknowns, query:typing.Union[None, str]=None):
    '''
    Prints the help message.
    '''
    query_found = True if query is None else False
    if query is None:
        print(getattr(sys.modules[__name__], '__doc__'))
        print(f"## Operations\n")

    for name,p in PARSERS.items():
        if query is None or name == query:
            print(f'### {name}')
            print('\n    '.join(p.format_help().split('\n')).replace('usage: ', '', 1))
            query_found = True
    if not query_found:
        raise Exception(f"Unknown gitp command '{query}' specified")


#ANCHOR: Universal Arguments
for name,p in PARSERS.items():
    p.add_argument('--verbosity', '-v', dest='verbosity', action='store', const=1, default=DEFAULT_DEBUG_LEVEL, nargs='?', type=int, help=argparse.SUPPRESS)
    p.add_argument('--color', dest='color', action='store', default=None, help=argparse.SUPPRESS)
    p.description = getattr(sys.modules[__name__], name).__doc__ or ''
    p.description = p.description.strip()


#ANCHOR: Main
def main():
    if len(sys.argv) == 1:
        help()
        sys.exit(0)
    orig_argv = [x for x in sys.argv]
    cmd = sys.argv.pop(1)

    if cmd == '--version':
        print(VERSION)
        sys.exit(0)

    #Intercept the git command with the gitp implementation
    if cmd in PARSERS:
        try:
            getattr(sys.modules[__name__], cmd)()
        except Exception as e:
            if DEBUG_LEVEL:
                raise
            print(style('Error: ' + str(e), Style.RED))
            sys.exit(1)

    #Run bare git command if it is not being intercepted by gitp
    if cmd not in PARSERS or GIT_FALLBACK:
        sys.argv.insert(1, cmd)
        try: 
            out = _git(('-c color.ui=always ' if sys.stdout.isatty() else '') + ' '.join(orig_argv[1:]))
            print(out, end='')
        except subprocess.CalledProcessError as e:
            print(e.output.decode('utf-8'), end='')
            sys.exit(1)

    sys.exit(CLI_RETURN_CODE)

if __name__ == '__main__':
    main()