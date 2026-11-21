// Muss vor jedem Matter.js-Import ausgewertet werden. Der Standardtest nutzt das virtuelle
// Matter-Netz; ein echtes UDP-Socket (und damit mDNS-Verkehr ins Netz) waere ein Fehler.
// Nur der MQTT-Broker auf 127.0.0.1 benutzt TCP.
import dgram from 'node:dgram';
import { syncBuiltinESMExports } from 'node:module';

dgram.createSocket = () => { throw new Error('OS-UDP ist im Standardtest gesperrt'); };
syncBuiltinESMExports();
