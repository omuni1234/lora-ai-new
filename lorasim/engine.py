from __future__ import annotations
from collections import Counter
from bisect import bisect_left
from dataclasses import dataclass, field
import hashlib
import importlib.metadata
import heapq
import itertools
import json
import math
from pathlib import Path
import platform
import time
import numpy as np
from . import __version__
from .components import Observation, Feedback, Stationary, make
from .config import Profile, Radio, dump_profile
from .phy import NS, ns, airtime_ns, links, destroyed_by, GW_SENSITIVITY, ED_SENSITIVITY
from .tracking import Tracker, atomic_json
from .interfaces import NetworkPolicy, Policy
from .energy import RadioMeter, compute_duration
from .acceleration import Geometry


@dataclass(slots=True)
class Attempt:
    message_id: int
    ed: int
    number: int
    generation_s: float
    packet_id: int = -1
    radio: Radio | None = None
    start: int = 0
    duration: int = 0
    done: bool = False
    gateways: dict = field(default_factory=dict)
    reply_scheduled: bool = False
    command: Radio | None = None
    command_computed: bool = False
    adr_ack_req: bool = False
    tx_index: int = 0
    outcomes: dict = field(default_factory=dict)
    xyz: tuple = (0.,0.,0.)
    link_report: tuple | None = None
    motion_state: str = "unknown"
    motion_epoch_start_ns: int | None = None
    selected_algorithm: str | None = None
    detected_motion_state: str | None = None


@dataclass(slots=True)
class Device:
    id: int
    group: object
    policy: object
    timing: object
    mobility: object
    rng: object
    state: str = "STANDBY"
    policy_label: str = "fixed"
    mobility_label: str = "stationary"
    energy: object = None
    mcu: object = None
    mcu_last: int = 0
    mcu_total: float = 0.
    pending_compute: list = field(default_factory=list)
    pending_timing: list = field(default_factory=list)
    window: int = 0
    frequency: int = 0
    sf: int = 7
    tx_count: int = 0
    last_ack: bool | None = None
    last_rssi: float | None = None
    last_radio: Radio | None = None
    context: Attempt | None = None
    duty: dict = field(default_factory=dict)
    trace_revision: int = -1


@dataclass(slots=True)
class Flight:
    id: int
    attempt: Attempt
    source: int
    direction: str
    start: int
    duration: int
    radio: Radio
    power: np.ndarray
    loss: np.ndarray
    distance: np.ndarray
    delay: np.ndarray
    @property
    def nbytes(self):
        return sum(a.nbytes for a in (self.power,self.loss,self.distance,self.delay))


@dataclass(slots=True)
class Reception:
    flight: Flight
    receiver: int
    start: int
    end: int
    energy: list = field(default_factory=lambda: [0.]*6)
    done: bool = False


class Simulation:
    """Nanosecond DES with stable FIFO event ordering and exact overlap accumulation.

    Flights are retained only until the last propagated end. Signals rejected by
    a receiver still contribute interference. ED uplink arrival callbacks are
    elided because Class A devices listen only to DOWN polarity; their interference
    contribution is retained in the flight arrays (no pruning by sensitivity).
    """
    def __init__(self, profile: Profile, output, progress=None, cancelled=None):
        self.p, self.output = profile,Path(output)
        self.now, self.end = 0,ns(profile.duration_s)
        self.queue, self.seq = [],itertools.count()
        self.packet_seq, self.message_seq = itertools.count(),itertools.count()
        self.flights, self.receptions = {},{}
        self.air_bytes = 0
        self.stats = Counter()
        self.progress_callback = progress or (lambda value: None)
        self.cancelled = cancelled or (lambda: False)
        self.action_keys = {(r.sf,r.frequency_hz,r.tp_dbm,r.cr) for r in profile.action_space.radios()}
        self.devices = self._devices()
        self.n = len(self.devices)
        self.gw_xyz = np.array([g.position for g in profile.gateways])
        self.gw_tx_until = [0]*len(profile.gateways)
        self.gw_duty = [{} for _ in profile.gateways]
        self.gw_locked = [set() for _ in profile.gateways]
        self.positions = np.vstack([[d.mobility.position(0) for d in self.devices],self.gw_xyz])
        self.moving = [d for d in self.devices if type(d.mobility) is not Stationary]
        self.geometry = Geometry(self) if profile.execution.optimized else None
        self.tracker = None
        self.listening = set()
        self.pending_uplinks = {}
        self.adr_servers = {d.id:d.policy.create_server() for d in self.devices if isinstance(d.policy,NetworkPolicy)}

    def _devices(self):
        from .mobility_environment import MobilityEnvironment
        environment = MobilityEnvironment(self.p.seed)
        devices = []
        for group in self.p.groups:
            placement_rng = np.random.default_rng(np.random.SeedSequence([self.p.seed, len(devices), 900]))
            for j in range(group.count):
                i = len(devices)
                if group.placement == "explicit":
                    initial = group.positions[j]
                elif group.placement == "grid":
                    side = math.ceil(math.sqrt(group.count))
                    initial = ((j%side+.5)*self.p.area_m[0]/side,(j//side+.5)*self.p.area_m[1]/side,1.5)
                else:
                    initial = (*placement_rng.uniform([0,0],self.p.area_m),1.5)
                specs = {k:getattr(group,k) for k in ("policy","timing","mobility")}
                for a in self.p.assignments:
                    if (not a.groups or group.name in a.groups) and (not a.ids or i in a.ids) and (not a.tags or set(a.tags)<=set(group.tags)):
                        for k in specs:
                            if getattr(a,k) is not None:
                                specs[k] = getattr(a,k)
                labels = {k:(v.ref or v.kind) for k,v in specs.items()}
                specs = {k:self.p.component(v,k) for k,v in specs.items()}
                rngs = [np.random.default_rng(np.random.SeedSequence([self.p.seed,i,k])) for k in range(4)]
                devices.append(Device(i,group,make(specs["policy"],"policy",rng=rngs[0],action_space=self.p.action_space),
                                      make(specs["timing"],"timing",rng=rngs[1]),
                                      make(specs["mobility"],"mobility",rng=rngs[2],initial=initial,area=self.p.area_m,environment=environment),rngs[3]))
                d = devices[-1]
                if getattr(d.policy,'requires_position',False) and not group.observe_position:
                    raise ValueError(f"{labels['policy']} requires observe_position=true")
                initialize = getattr(d.policy,'initialize',None)
                if callable(initialize) and getattr(type(d.policy),'initialize',None) is not Policy.initialize:
                    initialize(self.observation(d))
                if getattr(d.policy,"requires_confirmed",False) and not group.confirmed:
                    raise ValueError(f"{labels['policy']} requires confirmed uplinks for ACK learning")
                d.policy_label,d.mobility_label = labels["policy"],labels["mobility"]
                d.energy = RadioMeter(self.p.energy.radio)
                d.state = self.p.energy.radio.initial_state
                d.mcu = group.mcu or self.p.energy.mcu
        return devices

    def at(self, when, callback, *args):
        when = int(when)
        if when < self.now:
            raise ValueError("cannot schedule an event in the past")
        heapq.heappush(self.queue,(when,next(self.seq),callback,args))

    def observation(self,d):
        pos = tuple(d.mobility.position(self.now/NS)) if d.group.observe_position else None
        return Observation(self.now/NS,d.id,d.tx_count,d.last_ack,d.last_rssi,
                           d.last_radio.sf if d.last_radio else None,
                           d.last_radio.tp_dbm if d.last_radio else None,pos,
                           d.context.number if d.context else 1,d.group.max_attempts)

    def _state(self,d,state,ordinal=None,tp=None):
        ordinal = d.tx_count if ordinal is None else ordinal
        if d.mcu.enabled and d.mcu.idle_power_w and self.now > d.mcu_last:
            duration = (self.now-d.mcu_last)/NS
            energy = duration*d.mcu.idle_power_w
            self.tracker.write("energy",device_id=d.id,tx_index=d.energy.ordinal,component="mcu",
                               state="idle",start_ns=d.mcu_last,end_ns=self.now,duration_s=duration,
                               power_w=d.mcu.idle_power_w,energy_j=energy,mode="baseline")
            d.mcu_total += energy
        d.mcu_last = self.now
        d.energy.transition(self.now,state,ordinal,self.tracker,d.id,tp)
        d.state = state
        if state == "RX_ENABLED":
            self.listening.add(d.id)
        else:
            self.listening.discard(d.id)

    def _compute(self,d,operation,callback,arg,ordinal):
        timed = self.p.tracking.policy_diagnostics
        if hasattr(d.policy,"measure_meta_cpu"):
            d.policy.measure_meta_cpu = timed
            d.policy.meta_cpu_ns = 0
        cpu_start = time.process_time_ns() if timed else 0
        begin = time.perf_counter() if d.mcu.enabled and d.mcu.mode=="measured" else 0.
        result = callback(arg)
        measured = time.perf_counter()-begin if begin else 0.
        if timed:
            row = dict(device_id=d.id,tx_index=ordinal,time_ns=self.now,operation=operation,
                       cpu_ns=time.process_time_ns()-cpu_start,meta_cpu_ns=getattr(d.policy,"meta_cpu_ns",0))
            if operation=="inference":
                d.pending_timing.append(row)
            else:
                self.tracker.write("policy_timing",**row)
        if d.mcu.enabled:
            duration = compute_duration(d.mcu,d.policy,operation,measured)
            # Active energy is an increment above the always-on idle baseline.
            power = max(0.,d.mcu.active_power_w-d.mcu.idle_power_w)
            energy = power*duration
            row = dict(device_id=d.id,tx_index=ordinal,component="mcu",state=operation,
                       start_ns=self.now,end_ns=self.now,duration_s=duration,
                       power_w=power,energy_j=energy,mode=d.mcu.mode)
            if operation=="inference":
                d.pending_compute.append(row)
            else:
                self.tracker.write("energy",**row)
            d.mcu_total += energy
        return result

    def _uplink_result(self,ctx,censored=False):
        if self.pending_uplinks.pop(ctx.packet_id,None) is None:
            return
        values = set(ctx.outcomes.values())
        if "Received" in values:
            outcome = "Received"
        elif censored or len(ctx.outcomes)<len(self.p.gateways) or values & {"SimulationEnd","Cancelled"}:
            outcome = "Censored"
        else:
            outcome = next(iter(values)) if len(values)==1 else "MixedLoss"
        d = self.devices[ctx.ed]
        self.tracker.write("uplinks",packet_id=ctx.packet_id,message_id=ctx.message_id,device_id=ctx.ed,
                           tx_index=ctx.tx_index,attempt=ctx.number,time_ns=ctx.start,policy=d.policy_label,
                           outcome=outcome,received=outcome=="Received",gw_outcomes=json.dumps(ctx.outcomes),
                           x_m=ctx.xyz[0],y_m=ctx.xyz[1],z_m=ctx.xyz[2],motion_state=ctx.motion_state,motion_epoch_start_ns=ctx.motion_epoch_start_ns,
                           selected_algorithm=ctx.selected_algorithm,detected_motion_state=ctx.detected_motion_state)

    def _selector_trace(self, d):
        """Optional read-only change trace. No per-packet learner snapshots."""
        revision = getattr(d.policy, "trace_revision", None)
        if revision is not None and revision != d.trace_revision:
            self.tracker.write("selector_states", device_id=d.id, time_ns=self.now,
                               tx_index=d.tx_count, **d.policy.pool_trace())
            d.trace_revision = revision

    def event(self,name,ctx=None,detail=""):
        self.tracker.write("events",time_ns=self.now,device_id=ctx.ed if ctx else None,
                           packet_id=ctx.packet_id if ctx and ctx.packet_id>=0 else None,event=name,detail=detail)

    def run(self):
        if self.output.exists() and any(self.output.iterdir()):
            raise ValueError(f"output directory must be empty: {self.output}")
        self.output.mkdir(parents=True,exist_ok=True)
        text = dump_profile(self.p)
        (self.output/"profile.yaml").write_text(text)
        upstream = json.loads(Path(__file__).with_name("upstream.json").read_text())
        atomic_json(self.output/"manifest.json",{"schema_version":5,"simulator_version":__version__,
                    "profile_sha256":hashlib.sha256(text.encode()).hexdigest(),"upstream":upstream,
                    "python":platform.python_version(),"platform":platform.platform(),"numpy":np.__version__,
                    "time_resolution":"ns", "rng":"NumPy PCG64, SeedSequence(seed,device_id,component)",
                    "dependencies":{k:importlib.metadata.version(k) for k in ("numpy","pyarrow","pydantic","PyYAML")},
                    "source_sha256":{str(p.relative_to(Path(__file__).parent)):hashlib.sha256(p.read_bytes()).hexdigest() for p in Path(__file__).parent.rglob("*.py")}})
        self.tracker = Tracker(self.output,self.p.tracking.batch_rows,self.p.execution.writer_processes)
        start = time.perf_counter()
        last_wall, last_percent = start,-1
        status = "completed"
        try:
            for d in self.devices:
                xyz = self.positions[d.id]
                self.tracker.write("devices",device_id=d.id,group=d.group.name,policy=d.policy_label,mobility=d.mobility_label,x_m=xyz[0],y_m=xyz[1],z_m=xyz[2])
                self._selector_trace(d)
                self._next_application(d,None)
            self._progress("running",start)
            while self.queue:
                if self.stats["events_processed"]==0 and self.cancelled():
                    status="cancelled"
                    break
                when,_,cb,args = heapq.heappop(self.queue)
                if when >= self.end:
                    break
                self.now = when
                cb(*args)
                self.stats["events_processed"] += 1
                wall = time.perf_counter()
                percent = int(100*self.now/self.end)
                if wall-last_wall >= .25 or percent >= last_percent+1:
                    if self.cancelled():
                        status = "cancelled"
                        break
                    self._progress("running",start)
                    last_wall,last_percent = wall,percent
            if status == "completed":
                self.now = self.end
            for rx in list(self.receptions.values()):
                if not rx.done:
                    self._record(rx,"SimulationEnd" if status=="completed" else "Cancelled")
            for ctx in list(self.pending_uplinks.values()):
                self._uplink_result(ctx,True)
            for d in self.devices:
                self._state(d,d.state)
                for row in d.pending_compute:
                    row['tx_index'],row['state'] = 0,'inference_untransmitted'
                    self.tracker.write("energy",**row)
                d.pending_compute.clear()
                for row in d.pending_timing:
                    row["tx_index"] = 0  # selection never reached the air
                    self.tracker.write("policy_timing",**row)
                d.pending_timing.clear()
                if d.context and not d.context.done:
                    self.event("AttemptTruncated",d.context,status)
                    self.stats["attempts_truncated"] += 1
        except BaseException:
            self._progress("failed",start)
            raise
        finally:
            self.tracker.close()
        summary = self._progress(status,start)
        with (self.output/"policy_state.jsonl").open("w") as state_file:
            for d in self.devices:
                if hasattr(d.policy,"q") and hasattr(d.policy,"n"):
                    state_file.write(json.dumps({"device_id":d.id,"q":d.policy.q.tolist(),"n":d.policy.n.tolist()})+"\n")
                elif callable(getattr(d.policy,"snapshot",None)):
                    state_file.write(json.dumps({"device_id":d.id,"policy":d.policy_label,**d.policy.snapshot()},allow_nan=False)+"\n")
        summary["table_rows"] = self.tracker.counts
        summary["energy"] = {"radio_j":sum(d.energy.total for d in self.devices),"mcu_j":sum(d.mcu_total for d in self.devices)}
        from .analysis import analyze_run
        summary["abstract"] = analyze_run(self.output, include_mcu=True, series=False)
        summary["wall_s"] = time.perf_counter()-start
        atomic_json(self.output/"summary.json",summary)
        return summary

    def _progress(self,status,start):
        value = {"status":status,"sim_time_s":self.now/NS,"duration_s":self.p.duration_s,
                 "progress":self.now/self.end,"wall_s":time.perf_counter()-start,"counts":dict(self.stats)}
        atomic_json(self.output/"progress.json",value)
        self.progress_callback(value)
        return value

    def _next_application(self,d,previous):
        when = d.timing.next_time(self.observation(d),previous)
        if when is None:
            return
        if not math.isfinite(when) or when < self.now/NS or (previous is not None and ns(when)<=self.now):
            raise ValueError("timing must return None or a finite future time")
        self.at(ns(when),self._new_message,d)

    def _new_message(self,d):
        ctx = Attempt(next(self.message_seq),d.id,1,self.now/NS)
        d.context = ctx
        self.stats["messages_generated"] += 1
        self._attempt(d,ctx)

    def _attempt(self,d,ctx):
        if ctx.done:
            return
        if ctx.radio is None:
            selector = (lambda obs:d.policy.select_attempt(obs,ctx.number==1)) if isinstance(d.policy,NetworkPolicy) else d.policy.select
            ctx.radio = Radio.model_validate(self._compute(d,"inference",selector,self.observation(d),d.tx_count+1))
            if self.p.tracking.policy_diagnostics:
                trace = getattr(d.policy,"decision_trace",lambda:{})()
                ctx.selected_algorithm = trace.get("algorithm",d.policy_label)
                ctx.detected_motion_state = trace.get("motion_state")
        p = ctx.radio
        if hasattr(d.policy,'action_keys') and (p.sf,p.frequency_hz,p.tp_dbm,p.cr) not in d.policy.action_keys:
            raise ValueError(f"ED {d.id}: selector returned an action outside the common SF×CH×CR×TP / ED action space")
        b = self.p.direction_band(p.frequency_hz,"ul")
        if p.tp_dbm > self.p.sub_bands[b].max_tp_dbm:
            raise ValueError(f"ED {d.id}: TP exceeds sub-band maximum")
        max_payload = 51 if p.sf >= 10 else 115 if p.sf == 9 else 222
        if d.group.payload_bytes > max_payload:
            raise ValueError(f"ED {d.id}: payload exceeds EU DR limit for SF{p.sf}")
        wait = d.duty.get(b,0) if self.p.mac.duty_cycle else 0
        if wait > self.now:
            self.event("DutyCycleDeferred",ctx,str((wait-self.now)/NS))
            self.at(wait,self._attempt,d,ctx)
            return
        answer = isinstance(d.policy,NetworkPolicy) and d.policy.answer_pending
        phy_bytes = d.group.payload_bytes+9+(2 if answer else 0)
        ctx.start,ctx.duration = self.now,airtime_ns(phy_bytes,p)
        d.duty[b] = self.now+ns(ctx.duration/NS/self.p.sub_bands[b].duty_cycle)
        self._state(d,"TX",d.tx_count+1,p.tp_dbm)
        d.tx_count += 1
        ctx.tx_index = d.tx_count
        for row in d.pending_compute:
            row['tx_index'] = d.tx_count
            self.tracker.write("energy",**row)
        d.pending_compute.clear()
        for row in d.pending_timing:
            row["tx_index"] = d.tx_count
            self.tracker.write("policy_timing",**row)
        d.pending_timing.clear()
        d.last_radio = p
        ctx.adr_ack_req = isinstance(d.policy,NetworkPolicy) and d.policy.ack_requested
        self.stats["uplinks_sent"] += 1
        flight = self._transmit(d.id,"UL",ctx,p,phy_bytes)
        ctx.packet_id = flight.id
        ctx.xyz = tuple(self.positions[d.id])
        self.pending_uplinks[ctx.packet_id] = ctx
        self.at(self.now+ctx.duration+int(flight.delay[self.n:].max())+1,self._uplink_result,ctx)
        if isinstance(d.policy,NetworkPolicy):
            d.policy.answer_pending = False
            d.policy.sent(ctx.number==1)
        self.at(self.now+ctx.duration,self._tx_done,d,ctx)

    def _tx_done(self,d,ctx):
        if ctx.done:
            return
        self._state(d,"SLEEP")
        self.at(self.now+ns(self.p.mac.rx1_delay_s),self._open_window,d,ctx,1)
        self.at(self.now+ns(self.p.mac.rx2_delay_s),self._open_window,d,ctx,2)

    def _open_window(self,d,ctx,window):
        if ctx.done or d.context is not ctx or d.state == "RX_ACTIVE":
            return
        d.window = window
        d.frequency = ctx.radio.frequency_hz if window==1 else self.p.mac.rx2_frequency_hz
        d.sf = min(12,ctx.radio.sf+self.p.mac.rx1_sf_offset) if window==1 else self.p.mac.rx2_sf
        self._state(d,"RX_ENABLED")
        self.event(f"RX{window}Open",ctx)
        timeout = ns(self.p.mac.rx_symbols*2**d.sf/125000)
        self.at(self.now+timeout,self._close_window,d,ctx,window)

    def _close_window(self,d,ctx,window):
        if ctx.done or d.context is not ctx or d.window != window or d.state != "RX_ENABLED":
            return
        self._state(d,"SLEEP")
        self.event(f"RX{window}Timeout",ctx)
        if window==2:
            self._finish_attempt(d,ctx,False,None)

    def _finish_attempt(self,d,ctx,ack,rssi,downlink_received=False):
        if ctx.done:
            return
        ctx.done = True
        self._state(d,"SLEEP")
        known_ack = bool(ack) if d.group.confirmed else None
        d.last_ack,d.last_rssi = known_ack,rssi
        report = ctx.link_report if downlink_received and ctx.link_report else (None,None,None)
        f = Feedback(self.now/NS,known_ack,rssi,ctx.duration/NS,ctx.radio.tp_dbm,ctx.number,downlink_received,*report)
        self._compute(d,"training",d.policy.observe,f,d.tx_count)
        self._selector_trace(d)
        self.tracker.write("feedback",tx_index=ctx.tx_index,packet_id=ctx.packet_id,message_id=ctx.message_id,device_id=d.id,
                           time_ns=self.now,attempt=ctx.number,ack=known_ack,dl_rssi_dbm=rssi,
                           airtime_s=f.airtime_s,tx_power_dbm=f.tx_power_dbm,
                           ul_rssi_dbm=f.ul_rssi_dbm,ul_snr_db=f.ul_snr_db,ul_tx_power_dbm=f.ul_tx_power_dbm)
        self.stats["attempts_finished"] += 1
        if ack:
            self.stats["ack_received"] += 1
        if not (ack or downlink_received) and ctx.number < (1 if isinstance(d.policy,NetworkPolicy) and d.policy.reset_repetitions else d.group.max_attempts):
            next_ctx = Attempt(ctx.message_id,d.id,ctx.number+1,ctx.generation_s)
            d.context = next_ctx
            self.at(self.now+ns(d.rng.uniform(1,3)),self._attempt,d,next_ctx)
        else:
            d.context = None
            self._next_application(d,ctx.generation_s)

    def _transmit(self,source,direction,ctx,p,phy_bytes):
        if self.geometry:
            self.geometry.update(self.now/NS)
        for d in (() if self.geometry else self.moving):
            value = np.asarray(d.mobility.position(self.now/NS),dtype=float)
            if value.shape != (3,) or not np.isfinite(value).all():
                raise ValueError("mobility must return three finite coordinates")
            self.positions[d.id] = value
        xyz = self.positions[source].copy()
        if direction == "UL":
            mobility = self.devices[source].mobility
            state = mobility.motion_state(self.now/NS) if "motion_state" in type(mobility).__dict__ else "unknown"
            ctx.motion_state = state if state in ("stationary", "moving") else "unknown"
            epoch = getattr(mobility,"motion_epoch_start",lambda:None)()
            ctx.motion_epoch_start_ns = ns(epoch) if epoch is not None else None
        power,loss,distance,delay = self.geometry.links(source,p.tp_dbm) if self.geometry else links(xyz,self.positions,p.tp_dbm,self.p.channel,self.p.channel_backend)
        flight = Flight(next(self.packet_seq),ctx,source,direction,self.now,airtime_ns(phy_bytes,p),p,power,loss,distance,delay)
        if len(self.flights) >= self.p.tracking.max_air_signals or self.air_bytes+flight.nbytes > self.p.tracking.max_air_bytes:
            raise MemoryError("active-air budget exceeded; reduce offered traffic or raise tracking.max_air_bytes")
        # Account for each new signal once in every reception already locked.
        for rx in tuple(self.receptions.values()):
            self._add_energy(rx,flight)
        self.flights[flight.id] = flight
        self.air_bytes += flight.nbytes
        self.stats["peak_air_bytes"] = max(self.stats["peak_air_bytes"],self.air_bytes)
        self.tracker.write("transmissions",packet_id=flight.id,message_id=ctx.message_id,direction=direction,
                           device_id=ctx.ed,gateway_id=self.p.gateways[source-self.n].id if direction=="DL" else None,
                           attempt=ctx.number,tx_index=ctx.tx_index,adr=isinstance(self.devices[ctx.ed].policy,NetworkPolicy),
                           adr_ack_req=ctx.adr_ack_req if direction=="UL" else False,
                           link_adr_sf=ctx.command.sf if direction=="DL" and ctx.command else None,
                           link_adr_tp_dbm=ctx.command.tp_dbm if direction=="DL" and ctx.command else None,
                           link_report_bytes=getattr(self.devices[ctx.ed].policy,"link_feedback_bytes",0) if direction=="DL" else 0,
                           start_ns=self.now,end_ns=self.now+flight.duration,
                           sf=p.sf,cr=p.cr,bandwidth_hz=p.bandwidth_hz,frequency_hz=p.frequency_hz,
                           tx_power_dbm=p.tp_dbm,phy_bytes=phy_bytes,preamble_symbols=p.preamble_symbols,
                           implicit_header=p.implicit_header,crc=p.crc,x_m=xyz[0],y_m=xyz[1],z_m=xyz[2],
                           motion_state=ctx.motion_state if direction=="UL" else None,
                           motion_epoch_start_ns=ctx.motion_epoch_start_ns if direction=="UL" else None,
                           selected_algorithm=ctx.selected_algorithm if direction=="UL" else None,
                           detected_motion_state=ctx.detected_motion_state if direction=="UL" else None)
        for gi in range(len(self.p.gateways)):
            rid = self.n+gi
            if rid != source:
                self.at(self.now+int(delay[rid]),self._arrive,flight,rid)
        if direction=="DL":
            if self.p.execution.optimized:
                # Reserve the original FIFO ids, including equal-time ED arrivals.
                base = next(self.seq)
                self.seq = itertools.count(base+self.n)
                arrivals,ranks = self.geometry.arrivals(source,delay,base,self.now)
                self._schedule_arrival_batch(flight,arrivals,ranks,0)
            else:
                for rid in range(self.n):
                    self.at(self.now+int(delay[rid]),self._arrive,flight,rid)
        self.at(self.now+flight.duration+int(delay.max())+1,self._expire,flight.id)
        return flight

    def _schedule_arrival_batch(self,flight,arrivals,ranks,index):
        when,seq,_ = arrivals[index]
        heapq.heappush(self.queue,(when,seq,self._arrival_batch,(flight,arrivals,ranks,index)))

    def _arrival_batch(self,flight,arrivals,ranks,index):
        # Stop before ANY intervening event (also same-time FIFO ordering).
        # No observation of future receiver state; no change to interference.
        processed = 0
        while index < len(arrivals):
            stop = bisect_left(arrivals,(self.end,-1,-1),lo=index)
            if self.queue:
                stop = min(stop,bisect_left(arrivals,(*self.queue[0][:2],-1),lo=index))
            if stop == index:
                self._schedule_arrival_batch(flight,arrivals,ranks,index)
                break
            # Sleeping receivers cannot change state before the next queued event.
            # Retain the intended ED's rejected arrival for tracking.
            relevant = int(min((ranks[r] for r in self.listening | {flight.attempt.ed}
                            if ranks[r] >= index),default=len(arrivals)))
            skip_to = min(relevant,stop)
            if skip_to > index:
                self.now = arrivals[skip_to-1][0]
                processed += skip_to-index
                index = skip_to
                continue
            when,seq,r = arrivals[index]
            self.now = when
            self._arrive(flight,r)
            processed += 1
            index += 1
        # run() counts this callback once; preserve the logical event count.
        self.stats["events_processed"] += processed-1

    def _expire(self,packet_id):
        flight = self.flights.pop(packet_id,None)
        if flight:
            self.air_bytes -= flight.nbytes

    def _add_energy(self,rx,other):
        f,r = rx.flight,rx.receiver
        if other.id==f.id or other.source==r or other.radio.frequency_hz != f.radio.frequency_hz:
            return
        begin = other.start+int(other.delay[r])
        overlap = min(rx.end,begin+other.duration)-max(rx.start,begin)
        if overlap > 0:
            rx.energy[other.radio.sf-7] += overlap/NS * 10**((float(other.power[r])-30)/10)

    def _arrive(self,f,r):
        start = f.start+int(f.delay[r])
        rx = Reception(f,r,start,start+f.duration)
        reason = None
        if r >= self.n:
            gi = r-self.n
            g = self.p.gateways[gi]
            if self.gw_tx_until[gi] > self.now:
                reason = "LostBecauseTx"
            elif f.radio.frequency_hz not in g.frequencies_hz:
                reason = "WrongFrequency"
            elif f.direction != "UL":
                reason = "WrongPolarity"
            elif f.power[r] < GW_SENSITIVITY[f.radio.sf-7]:
                reason = "UnderSensitivity"
            elif len(self.gw_locked[gi]) >= g.demodulators:
                reason = "NoMoreReceivers"
        else:
            d = self.devices[r]
            if d.state != "RX_ENABLED":
                reason = "AlreadyReceiving" if d.state=="RX_ACTIVE" else "NotListening"
            elif f.radio.frequency_hz != d.frequency:
                reason = "WrongFrequency"
            elif f.radio.sf != d.sf:
                reason = "WrongSpreadingFactor"
            elif f.power[r] < ED_SENSITIVITY[f.radio.sf-7]:
                reason = "UnderSensitivity"
        if reason:
            if f.direction=="UL" or r==f.attempt.ed:
                self._record(rx,reason)
            return
        for other in self.flights.values():
            self._add_energy(rx,other)
        self.receptions[(r,f.id)] = rx
        if r>=self.n:
            self.gw_locked[r-self.n].add(f.id)
        else:
            self._state(self.devices[r],"RX_ACTIVE")
        self.at(rx.end,self._receive_end,rx)

    def _record(self,rx,outcome,destroyed=0):
        if rx.done:
            return
        rx.done = True
        f,r = rx.flight,rx.receiver
        self.receptions.pop((r,f.id),None)
        if r>=self.n:
            self.gw_locked[r-self.n].discard(f.id)
        if f.direction=="UL" and r>=self.n:
            f.attempt.outcomes[self.p.gateways[r-self.n].id] = outcome
        self.stats[f"rx_{outcome}"] += 1
        self.tracker.write("receptions",packet_id=f.id,message_id=f.attempt.message_id,direction=f.direction,
                           device_id=f.attempt.ed,receiver_kind="GW" if r>=self.n else "ED",
                           receiver_id=self.p.gateways[r-self.n].id if r>=self.n else str(r),
                           start_ns=rx.start,end_ns=rx.end,decision_ns=self.now,outcome=outcome,
                           rx_power_dbm=float(f.power[r]),tx_power_dbm=f.radio.tp_dbm,path_loss_db=float(f.loss[r]),
                           distance_m=float(f.distance[r]),delay_ns=int(f.delay[r]),sf=f.radio.sf,cr=f.radio.cr,
                           frequency_hz=f.radio.frequency_hz,destroyed_by_sf=destroyed,
                           interference_j=rx.energy if self.now>=rx.end else None)

    def _receive_end(self,rx):
        if rx.done:
            return
        f,r = rx.flight,rx.receiver
        signal_j = f.duration/NS * 10**((float(f.power[r])-30)/10)
        destroyed = destroyed_by(f.radio.sf,signal_j,rx.energy,self.p.channel.collision_matrix)
        outcome = "Interference" if destroyed else "Received"
        if r<self.n and r != f.attempt.ed and not destroyed:
            outcome = "NotAddressed"
        self._record(rx,outcome,destroyed)
        if r>=self.n and not destroyed:
            ctx = f.attempt
            if not ctx.gateways:
                self.stats["uplinks_received_any_gw"] += 1
            ctx.gateways[r-self.n] = float(f.power[r])
            if (self.devices[ctx.ed].group.confirmed or ctx.ed in self.adr_servers) and not ctx.reply_scheduled:
                ctx.reply_scheduled = True
                self.at(self.now+ns(self.p.mac.rx1_delay_s+self.p.mac.backhaul_delay_s),self._reply,ctx,1)
        elif r<self.n:
            d = self.devices[r]
            self._state(d,"SLEEP")
            ctx = d.context
            if ctx and not ctx.done:
                if outcome=="Received" and f.attempt is ctx:
                    if isinstance(d.policy,NetworkPolicy):
                        d.policy.downlink(ctx.command)
                        if ctx.command:
                            self.event("LinkADRReqApplied",ctx,f"sf={ctx.command.sf},tp={ctx.command.tp_dbm}")
                    self._finish_attempt(d,ctx,d.group.confirmed,float(f.power[r]),True)
                elif self.now >= ctx.start+ctx.duration+ns(self.p.mac.rx2_delay_s):
                    self._finish_attempt(d,ctx,False,None)

    def _reply(self,ctx,window):
        if ctx.done:
            return
        d = self.devices[ctx.ed]
        if ctx.ed in self.adr_servers and not ctx.command_computed:
            ctx.command = self.adr_servers[ctx.ed].request(ctx.message_id,ctx.radio,ctx.gateways.values())
            ctx.command_computed = True
        if ctx.ed in self.adr_servers and not d.group.confirmed and not ctx.command and not ctx.adr_ack_req:
            return
        freq = ctx.radio.frequency_hz if window==1 else self.p.mac.rx2_frequency_hz
        band = self.p.band(freq)
        allowed = self.p.dl_sub_bands
        if allowed is not None and self.p.sub_bands[band].id not in allowed:
            self.event(f"RX{window}SubBandDisabled",ctx)
            if window == 1:
                self.at(self.now+ns(self.p.mac.rx2_delay_s-self.p.mac.rx1_delay_s),self._reply,ctx,2)
            return
        candidates = sorted(ctx.gateways,key=lambda g:(-ctx.gateways[g],g))
        selected = next((g for g in candidates if self.gw_tx_until[g]<=self.now and
                         (not self.p.mac.duty_cycle or self.gw_duty[g].get(band,0)<=self.now)),None)
        if selected is None:
            self.event(f"RX{window}NoGateway",ctx)
            if window==1:
                self.at(self.now+ns(self.p.mac.rx2_delay_s-self.p.mac.rx1_delay_s),self._reply,ctx,2)
            return
        sf = min(12,ctx.radio.sf+self.p.mac.rx1_sf_offset) if window==1 else self.p.mac.rx2_sf
        p = Radio(sf=sf,frequency_hz=freq,tp_dbm=self.p.sub_bands[band].max_tp_dbm)
        telemetry_bytes = getattr(d.policy,"link_feedback_bytes",0)
        if telemetry_bytes:
            # Research telemetry: one type byte + two float32 link measurements.
            # It is delivered only after successful PHY/MAC reception of this DL.
            rssi = ctx.gateways[selected]
            ctx.link_report = (float(np.float32(rssi)),float(np.float32(rssi+174-10*math.log10(125000)-6)),ctx.radio.tp_dbm)
        phy_bytes = 9+(5 if ctx.command else 0)+telemetry_bytes
        duration = airtime_ns(phy_bytes,p)
        for fid in tuple(self.gw_locked[selected]):
            self._record(self.receptions[(self.n+selected,fid)],"LostBecauseTx")
        self.gw_tx_until[selected] = self.now+duration
        self.gw_duty[selected][band] = self.now+ns(duration/NS/self.p.sub_bands[band].duty_cycle)
        self.stats["downlinks_sent"] += 1
        self._transmit(self.n+selected,"DL",ctx,p,phy_bytes)
