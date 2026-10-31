"""External configuration reuse without copying credentials into the checkout."""
import os
from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).parents[1]


def test_external_env_loads_but_official_runtime_proxy_and_key_win(tmp_path):
    path=tmp_path/'external.env'
    path.write_text('LLM_NAME=source-model\nLLM_API_KEY=file-key\nLLM_BASE_URL=https://file.invalid/v1\n')
    env=dict(os.environ)
    for key in ('LLM_API_KEY','API_KEY','BASE_URL','LLM_BASE_URL','LLM_NAME','MODEL'):
        env.pop(key,None)
    env.update(FACT_ATTACK_ENV_FILE=str(path),API_KEY='runtime-key',BASE_URL='https://proxy.invalid/v1')
    code="from src import config; assert config.LLM_NAME=='source-model'; assert config.LLM_API_KEY=='runtime-key'; assert config.LLM_BASE_URL=='https://proxy.invalid/v1'; print('configuration_ok')"
    result=subprocess.run([sys.executable,'-c',code],cwd=ROOT,env=env,text=True,capture_output=True)
    assert result.returncode==0,result.stderr
    assert result.stdout.strip()=='configuration_ok'


def test_missing_explicit_env_is_not_silently_mocked(tmp_path):
    env={**os.environ,'FACT_ATTACK_ENV_FILE':str(tmp_path/'missing.env')}
    result=subprocess.run([sys.executable,'-c','from src import config'],cwd=ROOT,env=env,text=True,capture_output=True)
    assert result.returncode!=0
    assert 'FACT_ATTACK_ENV_FILE points to a missing configuration file' in result.stderr
