"""Gateway state fixtures shared with the compiled ns3-oracle.cc harness."""
import heapq
from pathlib import Path
import numpy as np
import pyarrow.parquet as pq
import pytest
from lorasim.config import Profile,Radio
from lorasim.engine import Simulation,Attempt,Flight
from lorasim.tracking import Tracker

@pytest.mark.parametrize('scenario,expected',[
 ('sensitivity',[(1,'UnderSensitivity',0),(2,'Received',1000000000)]),
 ('capacity',[(3,'NoMoreReceivers',0),(1,'Received',1000000000),(2,'Received',1000000000)]),
 ('interference',[(1,'Interference',1000000000),(2,'Interference',1500000000)]),
 ('tx_interrupt',[(1,'LostBecauseTx',500000000)])])
def test_actual_engine_gateway_fixtures(tmp_path,scenario,expected):
    p=Profile.model_validate({'duration_s':3,'mac':{'duty_cycle':False},'groups':[{'name':'ed','count':1,'confirmed':False}],
                              'gateways':[{'id':'gw','position':[0,0,0],'demodulators':2}]})
    sim=Simulation(p,tmp_path/scenario);sim.tracker=Tracker(tmp_path/scenario)
    ctx=Attempt(0,0,1,0,radio=Radio());sim.devices[0].context=ctx
    def arrival(fid,sf,power):
        radio=Radio(sf=sf)
        flight=Flight(fid,ctx,0,'UL',sim.now,1000000000,radio,np.array([power,power]),np.array([14-power]*2),np.array([0.,0.]),np.array([0,0]))
        for rx in tuple(sim.receptions.values()):sim._add_energy(rx,flight)
        sim.flights[fid]=flight;sim._arrive(flight,1)
    if scenario=='sensitivity':
        arrival(1,7,-130.01);arrival(2,8,-132.5)
    elif scenario=='capacity':
        for i in range(3):arrival(i+1,7+i,-100)
    elif scenario=='interference':
        arrival(1,7,-100);sim.at(500000000,arrival,2,7,-100)
    else:
        arrival(1,7,-100)
        replyctx=Attempt(1,0,1,0,radio=Radio(),gateways={0:-100})
        # Use non-conflicting packet ids for the independently created downlink.
        sim.packet_seq=iter(range(100,200));sim.at(500000000,sim._reply,replyctx,1)
    while sim.queue:
        now,_,fn,args=heapq.heappop(sim.queue)
        sim.now=now;fn(*args)
    sim.tracker.close()
    rows=pq.read_table(tmp_path/scenario/'receptions').to_pylist()
    actual=[(r['packet_id'],r['outcome'],r['decision_ns']) for r in rows if r['direction']=='UL']
    assert actual==expected
