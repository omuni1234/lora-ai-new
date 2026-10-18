// SPDX-License-Identifier: GPL-2.0-only
// Evaluates the actual pinned ns-3/lorawan implementations; not copied equations.
#include "ns3/core-module.h"
#include "ns3/mobility-module.h"
#include "ns3/propagation-module.h"
#include "ns3/lorawan-module.h"
#include <iomanip>
#include <iostream>
using namespace ns3;
using namespace ns3::lorawan;
static std::string scenario;
static void Report(std::string reason, Ptr<const Packet> p, uint32_t node) {
    std::cout << "GW," << scenario << ',' << p->GetSize() << ',' << reason << ',' << Simulator::Now().GetNanoSeconds() << '\n';
}
int main() {
    std::cout << std::setprecision(17);
    for (uint8_t sf=7;sf<=12;sf++) for (uint8_t cr=1;cr<=4;cr++)
    for (uint32_t bytes: {9u,20u,29u,51u,123u,230u}) {
        LoraTxParameters p;
        p.spreadingFactor=sf; p.bandwidthHz=125000; p.codingRate=static_cast<CodingRate>(cr);
        p.lowDataRateOptimize=sf>=11; p.preambleLenSymb=8; p.implicitHeader=false; p.crcEnabled=true;
        std::cout << "AIR," << bytes << ',' << +sf << ',' << +cr << ',' << LoraPhy::GetTimeOnAir(bytes,p).GetNanoSeconds() << '\n';
    }
    auto loss=CreateObject<LogDistancePropagationLossModel>(); loss->SetPathLossExponent(3.76);loss->SetReference(1,7.7);
    auto delay=CreateObject<ConstantSpeedPropagationDelayModel>();
    auto a=CreateObject<ConstantPositionMobilityModel>(); auto b=CreateObject<ConstantPositionMobilityModel>();
    for (double d: {0.,.5,1.,10.,100.,1000.,10000.}) {
        b->SetPosition(Vector(d,0,0));
        std::cout << "PATH," << d << ',' << loss->CalcRxPower(14,a,b) << ',' << delay->GetDelay(a,b).GetNanoSeconds() << '\n';
    }
    for (uint8_t sf=7;sf<=12;sf++) for (uint8_t other=7;other<=12;other++)
    for (double delta: {-40.,-20.,0.,6.,20.,40.}) for (double start: {0.,.5,.95,1.}) {
        LoraInterferenceHelper h;
        auto target=h.Add(Seconds(1),-100,sf,Create<Packet>(20),868100000);
        Simulator::Schedule(Seconds(start),[&h,other,delta](){h.Add(Seconds(1),-100+delta,other,Create<Packet>(20),868100000);});
        Simulator::Schedule(Seconds(1),[&h,target,sf,other,delta,start](){
            std::cout << "COL," << +sf << ',' << +other << ',' << delta << ',' << start << ',' << +h.IsDestroyedByInterference(target) << '\n';
        });
        Simulator::Run();Simulator::Destroy();
    }
    for (const std::string name: {"sensitivity","capacity","interference","tx_interrupt"}) {
        scenario=name;
        auto gw=CreateObject<SimpleGatewayLoraPhy>();gw->AddFrequency(868100000);gw->AddReceptionPath();gw->AddReceptionPath();
        gw->TraceConnectWithoutContext("ReceivedPacket",MakeBoundCallback(&Report,std::string("Received")));
        gw->TraceConnectWithoutContext("LostPacketBecauseInterference",MakeBoundCallback(&Report,std::string("Interference")));
        gw->TraceConnectWithoutContext("LostPacketBecauseUnderSensitivity",MakeBoundCallback(&Report,std::string("UnderSensitivity")));
        gw->TraceConnectWithoutContext("LostPacketBecauseNoMoreReceivers",MakeBoundCallback(&Report,std::string("NoMoreReceivers")));
        gw->TraceConnectWithoutContext("NoReceptionBecauseTransmitting",MakeBoundCallback(&Report,std::string("LostBecauseTx")));
        auto channel=CreateObject<LoraChannel>(loss,delay);gw->SetChannel(channel);gw->SetMobility(a);
        if(name=="sensitivity") {
            gw->StartReceive(Create<Packet>(1),868100000,IQPolarity::UP,7,-130.01,Seconds(1));
            gw->StartReceive(Create<Packet>(2),868100000,IQPolarity::UP,8,-132.5,Seconds(1));
        } else if(name=="capacity") {
            for(int i=0;i<3;i++)gw->StartReceive(Create<Packet>(i+1),868100000,IQPolarity::UP,7+i,-100,Seconds(1));
        } else if(name=="interference") {
            gw->StartReceive(Create<Packet>(1),868100000,IQPolarity::UP,7,-100,Seconds(1));
            Simulator::Schedule(Seconds(.5),[gw](){gw->StartReceive(Create<Packet>(2),868100000,IQPolarity::UP,7,-100,Seconds(1));});
        } else {
            gw->StartReceive(Create<Packet>(1),868100000,IQPolarity::UP,7,-100,Seconds(1));
            Simulator::Schedule(Seconds(.5),[gw](){LoraTxParameters p;gw->Send(Create<Packet>(9),869525000,IQPolarity::DOWN,p,14);});
        }
        Simulator::Run();Simulator::Destroy();
    }
}
