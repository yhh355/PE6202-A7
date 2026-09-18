"""Resume the preserved live v2 trials locally, without re-running completed trials."""
import getpass
import hashlib
import json
import os
from pathlib import Path
import statistics
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'A2_scaffold'))
import config
import harness
from agent import run_case
from live_budget import LiveBudget

RUN = ROOT/'FengHao/evidence/live_final'


def save(path,value):
    temporary=path.with_suffix(path.suffix+'.tmp')
    temporary.write_text(json.dumps(value,indent=2,ensure_ascii=False),encoding='utf-8')
    temporary.replace(path)


def read_rows(path):
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()] if path.exists() else []


def main():
    manifest=json.loads((RUN/'manifest.json').read_text())
    for name,digest in manifest['source_hashes'].items():
        if hashlib.sha256((ROOT/name).read_bytes()).hexdigest()!=digest:
            raise RuntimeError('代码或数据已改变，不能继续同一实验：'+name)
    key=harness.load_key('B')
    plan=[(cid,t) for cid in manifest['case_ids']
          for t in range(1,(3 if harness._is_negative(key[cid]) else 1)+1)]
    folder=RUN/'v2'
    rows=read_rows(folder/'trials.jsonl')
    completed={(r['case_id'],r['trial']) for r in rows}
    if len(completed)!=len(rows):
        raise RuntimeError('发现重复试验记录，请先检查')
    pending=[item for item in plan if item not in completed]
    print('v2 已完成 %d/%d，待运行 %d；v1 不会重跑。'%(len(rows),len(plan),len(pending)))
    if not pending and (folder/'results.json').exists():
        print('v2 已经全部完成。结果在：',folder/'results.json')
        return
    ledger=read_rows(RUN/'api_usage.jsonl')
    spent=sum(max(r['standard_price_cost_usd'],r.get('provider_cost_usd') or 0) for r in ledger)
    print('已记录的标准价格预算消耗约 US$%.4f，总预算 US$0.50。'%spent)
    print('上次中断时的在途请求可能产生少量未记录费用，以 OpenRouter 账单为准。')
    print('仅调用 OpenRouter；预约均为代码验证后的本地模拟。输入密钥即继续本次已授权实验。')
    config.API_KEY=os.environ.get('OPENROUTER_API_KEY') or getpass.getpass('请粘贴 OpenRouter 密钥（不会显示），然后回车：').strip()
    if not config.API_KEY:
        print('没有输入密钥，已退出。')
        return
    if (not config.API_KEY.isascii() or any(c.isspace() for c in config.API_KEY)
            or not config.API_KEY.startswith('sk-or-')):
        config.API_KEY=''
        print('密钥格式不正确：请只复制密钥本身，不要包含邮件说明、中文、引号或空格。未发送请求，请重新运行。')
        return
    config.BACKEND,config.PROBLEM,config.AUTONOMY='live','B','confirm'
    config.MODEL=manifest['model']
    config.PRICE_IN=manifest['prices_per_million']['input']
    config.PRICE_OUT=manifest['prices_per_million']['output']
    config.LIVE_BUDGET=LiveBudget(0.50,config.PRICE_IN,config.PRICE_OUT,RUN/'api_usage.jsonl')
    config.LIVE_BUDGET.spent=spent
    # Refuse simultaneous resume processes. A forced crash may leave the lock;
    # remove it only after checking no other resume window is running.
    lock=RUN/'resume_v2.lock'
    fd=os.open(lock,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
    os.write(fd,str(os.getpid()).encode());os.close(fd)
    try:
        manifest['status']='running'
        manifest['resumed_by']='user local terminal'
        save(RUN/'manifest.json',manifest)
        for cid,trial in pending:
            action_log=folder/'actions'/f'{cid}_{trial}.jsonl'
            if action_log.exists():
                raise RuntimeError('中断试验已有动作日志，需核查后才能重跑：'+str(action_log))
            record=run_case(cid,version='v2',approve=lambda action,payload:action=='book_slot',log_path=action_log)
            if not record.get('usage_records'):
                save(RUN/'last_resume_error.json', {'case_id':cid,'trial':trial,
                    'reason':record.get('reason'), 'stopped_by':record.get('stopped_by'),
                    'counted_as_completed':False})
                raise RuntimeError('本次没有收到模型响应，已暂停，未计入完成次数。请检查密钥、网络或错误记录。')
            if record['action_log']:
                record['action_log']=str(Path(record['action_log']).relative_to(RUN))
            passed,fails=harness.code_check(record,key[cid])
            row=dict(case_id=cid,trial=trial,passed=passed,fails=fails,record=record)
            with (folder/'trials.jsonl').open('a',encoding='utf-8') as f:
                f.write(json.dumps(row,ensure_ascii=False)+'\n')
            rows.append(row)
            print('[%d/%d] %s 第%d次 %s'%(len(rows),len(plan),cid,trial,'PASS' if passed else 'FAIL'),flush=True)
        queue=[]
        returned=[]
        for row in rows:
            judge=harness.prepare_judgement_check(row['record'],key[row['case_id']])
            judge['trial']=row['trial'];queue.append(judge)
            for event in row['record']['trace']:
                if event['tool']=='check_referral_criteria':
                    returned.append({'case_id':row['case_id'],'trial':row['trial'],
                        'returned_json':json.dumps(event['observation'],ensure_ascii=False)})
        summary={'trials':len(rows),'code_passed':sum(r['passed'] for r in rows),
            'code_pass_rate':sum(r['passed'] for r in rows)/len(rows),
            'median_turns':statistics.median(r['record']['turns'] for r in rows),
            'tokens_in':sum(r['record']['tokens_in'] for r in rows),
            'tokens_out':sum(r['record']['tokens_out'] for r in rows),
            'cost_usd':sum(r['record']['cost_usd'] for r in rows),
            'measurement_kind':'api_usage','judgement_status':'pending; code pass is not overall pass',
            'criteria_calls':len(returned)}
        save(folder/'results.json',{'summary':summary,'results':rows,'judgement_queue':queue})
        save(folder/'returned_observations.json',returned)
        comparison=json.loads((RUN/'comparison.json').read_text())
        comparison['v2']=summary;save(RUN/'comparison.json',comparison)
        manifest['status']='completed'
        manifest.pop('error_type',None)
        manifest['note']='Resumed after interruption. Completed trial usage is recorded; the interrupted in-flight request may have additional billing. AI/human judgement remains pending.'
        print('\nv2 已全部完成！代码检查通过 %d/%d。'%(summary['code_passed'],summary['trials']))
        print('结果已保存到：',folder/'results.json')
        print('现在可以回到 Codex，说“v2 跑完了，请分析结果”。')
    except BaseException as error:
        manifest['status']='interrupted_or_failed'
        manifest['error_type']=type(error).__name__
        print('\n运行暂停：'+type(error).__name__+'。已完成结果已保存；再次双击可以继续。')
        if isinstance(error,RuntimeError):print(str(error))
        raise
    finally:
        config.API_KEY=''
        save(RUN/'manifest.json',manifest)
        lock.unlink(missing_ok=True)


if __name__=='__main__':
    main()
