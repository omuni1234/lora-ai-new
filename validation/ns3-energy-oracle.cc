// SPDX-License-Identifier: GPL-2.0-only
// Calls the upstream objects directly; no reimplementation of their equations.
#include "ns3/core-module.h"
#include "ns3/network-module.h"
#include "ns3/energy-module.h"
#include "ns3/lorawan-module.h"
#include <iomanip>
#include <iostream>
using namespace ns3;
using namespace ns3::lorawan;
int main() {
    std::cout << std::setprecision(17);
    auto tx = CreateObject<LinearLoraTxCurrentModel>();
    for(double power: {-20.,0.,2.,8.,13.,14.,20.,27.})
        std::cout << "CURRENT," << power << ',' << tx->CalcTxCurrent(power) << '\n';
    for (bool linear: {false,true}) {
        auto source = CreateObject<energy::BasicEnergySource>();
        source->SetNode(CreateObject<Node>());
        source->SetInitialEnergy(100000);
        auto model = CreateObject<LoraRadioEnergyModel>();
        model->SetEnergySource(source);
        source->AppendDeviceEnergyModel(model);
        if(linear)model->SetTxCurrentModel(tx);
        auto listener=model->GetPhyListener();
        Simulator::Schedule(Seconds(1),[listener](){listener->NotifyTx(14);});
        Simulator::Schedule(Seconds(3),[listener](){listener->NotifySleep();});
        Simulator::Schedule(Seconds(4),[listener](){listener->NotifyRxEnabled();});
        Simulator::Schedule(Seconds(5),[listener](){listener->NotifyRxActive();});
        Simulator::Schedule(Seconds(6),[listener](){listener->NotifyStandby();});
        Simulator::Schedule(Seconds(7),[model,linear](){std::cout << "ENERGY," << linear << ',' << model->GetTotalEnergyConsumption() << '\n';});
        Simulator::Stop(Seconds(7.000001));
        Simulator::Run();Simulator::Destroy();
    }
}
