import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
from .config import load_profile, dump_profile
from .engine import Simulation
from .jobs import Store,worker,Notifier


def main():
    parser = argparse.ArgumentParser(description="LoRaSim research simulator")
    sub = parser.add_subparsers(dest="command",required=True)
    p = sub.add_parser("run"); p.add_argument("profile"); p.add_argument("--output",required=True)
    p = sub.add_parser("validate"); p.add_argument("profile")
    for name in ("submit","sweep","worker","start-worker","jobs","cancel","serve","import-examples"):
        p = sub.add_parser(name); p.add_argument("--data",default=os.getenv("LORASIM_DATA","data"))
        if name in ("submit","sweep"):
            p.add_argument("profile")
            p.add_argument("--experiment-id")
        if name=="sweep":
            p.add_argument("--seeds",nargs="+",type=int,required=True)
        if name in ("worker","start-worker"):
            p.add_argument("--workers",type=int,default=None,help="同時Run数の初期値（省略時: CPU数と2の小さい方）。保存済みGUI設定が優先されます。")
        if name=="worker":
            p.add_argument("--once",action="store_true")
        if name=="cancel":
            p.add_argument("id")
        if name=="serve":
            p.add_argument("--host",default="127.0.0.1"); p.add_argument("--port",type=int,default=8000)
        if name=="import-examples":
            p.add_argument("--examples",default="examples")
    args = parser.parse_args()
    if args.command=="run":
        p=load_profile(args.profile)
        notifier=Notifier(p.notifications,args.output,"foreground") if p.notifications else None
        try:
            result = Simulation(p,args.output,progress=notifier.push if notifier else None).run()
        finally:
            if notifier:
                notifier.close()
        print(json.dumps(result,indent=2))
    elif args.command=="validate":
        p = load_profile(args.profile)
        Simulation(p,"unused-validation-output")
        print(f"Valid: {p.name}, {sum(g.count for g in p.groups)} EDs")
    elif args.command=="serve":
        import uvicorn
        from .api import create_app
        uvicorn.run(create_app(args.data),host=args.host,port=args.port)
    elif args.command=="worker":
        worker(args.data,args.workers,args.once)
    elif args.command=="start-worker":
        root = Path(args.data).resolve(); root.mkdir(parents=True,exist_ok=True)
        with (root/"worker.log").open("a") as log:
            process = subprocess.Popen([sys.executable,"-m","lorasim","worker","--data",str(root)]+(["--workers",str(args.workers)] if args.workers is not None else []),
                                       stdin=subprocess.DEVNULL,stdout=log,stderr=log,start_new_session=True)
        print(json.dumps({"pid":process.pid,"log":str(root/"worker.log")}))
    else:
        store = Store(args.data)
        if args.command=="submit":
            print(store.submit(dump_profile(load_profile(args.profile,catalog=store.catalog())),args.experiment_id))
        elif args.command=="sweep":
            p = load_profile(args.profile)
            eid = args.experiment_id or store.create_experiment(p.name)
            for seed in args.seeds:
                q = p.model_copy(update={"seed":seed,"name":f"{p.name}-seed-{seed}"})
                q = type(p).model_validate(q.model_dump())
                print(store.submit(dump_profile(q),eid))
        elif args.command=="jobs":
            print(json.dumps(store.list(),indent=2))
        elif args.command=="cancel":
            store.cancel(args.id)
        elif args.command=="import-examples":
            for file in sorted(Path(args.examples).glob("*.yaml")):
                store.save_profile(file.stem,dump_profile(load_profile(file)))
                print(file.stem)

if __name__=="__main__":
    main()
