"""Editable defaults exposed to the GUI; executable implementations stay in modules."""
DEFAULTS = {
 'policy':{
  'fixed':{'sf':7,'tp_dbm':14,'cr':1,'frequency_hz':868100000,'bandwidth_hz':125000,'preamble_symbols':8,'implicit_header':False,'crc':True},
  'random':{'actions':[{'sf':7,'tp_dbm':14},{'sf':9,'tp_dbm':14},{'sf':12,'tp_dbm':14}]},
  'bandit':{'actions':[{'sf':7,'tp_dbm':8},{'sf':9,'tp_dbm':12},{'sf':12,'tp_dbm':14}],'epsilon':.1,'energy_weight':.02},
  'adr':{'initial':{'sf':12,'tp_dbm':14},'history_range':20,'gateway_combine':'max','history_combine':'max','change_tx_power':True,'margin_db':0.,'ack_limit':64,'ack_delay':32,'backoff':True},
  'badr':{'sequence':[12,7,10,7,10,7],'random_phase':False,'tp_dbm':14,'cr':1,'frequency_hz':868100000}},
 'mobility':{'stationary':{},'random_walk':{'speed_m_s':1.5,'step_s':10.},'waypoints':{'points':[[0,100,100,1.5],[300,1800,1800,1.5]]}},
 'timing':{'periodic':{'interval_s':60.,'first_s':0.,'random_phase':True,'jitter_s':0.},'poisson':{'mean_s':60.}}
}
