import os
from pathlib import Path
import shutil
import subprocess


def test_update_collects_official_bio_even_if_fcinfo_fails(tmp_path):
    scripts = tmp_path / 'scripts'
    scripts.mkdir()
    shutil.copyfile(Path(__file__).parents[1] / 'scripts/update.sh', scripts / 'update.sh')
    activate = tmp_path / '.venv/bin/activate'
    activate.parent.mkdir(parents=True)
    activate.touch()
    bin_dir = tmp_path / 'bin'
    bin_dir.mkdir()
    command = bin_dir / 'gksave'
    command.write_text('''#!/bin/bash
printf '%s\\n' "$*" >> "$COMMAND_LOG"
if [[ "$1" == "playerinfo" ]]; then exit 1; fi
''')
    command.chmod(0o755)
    log = tmp_path / 'commands.txt'
    env = {**os.environ, 'PATH': str(bin_dir) + os.pathsep + os.environ['PATH'],
           'COMMAND_LOG': str(log)}
    result = subprocess.run(['bash', str(scripts / 'update.sh')], env=env,
                            capture_output=True, text=True)
    assert result.returncode == 0
    commands = log.read_text().splitlines()
    assert 'playerdetail --defenders --limit 120' in commands
    assert commands.index('playerdetail --defenders --limit 120') < next(i for i, c in enumerate(commands) if c.startswith('export '))
