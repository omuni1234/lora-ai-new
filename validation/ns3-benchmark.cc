// SPDX-License-Identifier: GPL-2.0-only
#include "ns3/core-module.h"
#include "ns3/lorawan-module.h"
#include "ns3/mobility-module.h"
#include <chrono>
#include <iostream>
using namespace ns3; using namespace ns3::lorawan;
static uint64_t received=0,sent=0;
static void Received(Ptr<const Packet>,uint32_t){received++;}
static void Sent(Ptr<const Packet>,uint32_t){sent++;}
int main(int argc,char**argv){
    uint32_t n=1000;double duration=3600;
    CommandLine cmd;cmd.AddValue("n","ED count",n);cmd.AddValue("duration","duration seconds",duration);cmd.Parse(argc,argv);
    auto loss=CreateObject<LogDistancePropagationLossModel>();loss->SetPathLossExponent(3.76);loss->SetReference(1,7.7);
    auto delay=CreateObject<ConstantSpeedPropagationDelayModel>();auto channel=CreateObject<LoraChannel>(loss,delay);
    MobilityHelper mob;auto positions=CreateObject<ListPositionAllocator>();
    auto side=static_cast<uint32_t>(std::ceil(std::sqrt(n)));
    for(uint32_t i=0;i<n;i++)positions->Add(Vector((i%side+.5)*2000/side,(i/side+.5)*2000/side,1.5));
    positions->Add(Vector(1000,1000,15));mob.SetPositionAllocator(positions);mob.SetMobilityModel("ns3::ConstantPositionMobilityModel");
    NodeContainer eds;eds.Create(n);mob.Install(eds);NodeContainer gws;gws.Create(1);mob.Install(gws);
    LoraPhyHelper ph;ph.SetChannel(channel);LorawanMacHelper mac;mac.SetRegion(LorawanMacHelper::SingleChannel);LoraHelper helper;
    ph.SetDeviceType(LoraPhyHelper::ED);mac.SetDeviceType(LorawanMacHelper::ED_A);helper.Install(ph,mac,eds);
    ph.SetDeviceType(LoraPhyHelper::GW);mac.SetDeviceType(LorawanMacHelper::GW);helper.Install(ph,mac,gws);
    auto gw=DynamicCast<LoraNetDevice>(gws.Get(0)->GetDevice(0));gw->GetPhy()->TraceConnectWithoutContext("ReceivedPacket",MakeCallback(&Received));
    for(uint32_t i=0;i<n;i++){
        auto dev=DynamicCast<LoraNetDevice>(eds.Get(i)->GetDevice(0));auto emac=DynamicCast<ClassAEndDeviceLorawanMac>(dev->GetMac());
        emac->SetDataRate(5);emac->SetTransmissionPowerDbm(14);emac->SetAttribute("ADR",BooleanValue(false));
        dev->GetPhy()->TraceConnectWithoutContext("StartSending",MakeCallback(&Sent));
        for(double t=i*0.1;t<duration;t+=300)Simulator::Schedule(Seconds(t),[emac](){emac->Send(Create<Packet>(20));});
    }
    Simulator::Stop(Seconds(duration));auto start=std::chrono::steady_clock::now();Simulator::Run();
    double elapsed=std::chrono::duration<double>(std::chrono::steady_clock::now()-start).count();
    std::cout<<"{\"n\":"<<n<<",\"duration_s\":"<<duration<<",\"wall_s\":"<<elapsed<<",\"sent\":"<<sent<<",\"received\":"<<received<<"}"<<std::endl;
    Simulator::Destroy();
}
