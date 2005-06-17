import socket, string, types
from pysnmp.entity import engine, config
from pysnmp.entity.rfc3413 import cmdgen, mibvar
from pysnmp.carrier.asynsock.dgram import udp
from pysnmp.smi import view
from pysnmp.entity.rfc3413.error import ApplicationReturn
from pysnmp import error
from pyasn1.type import univ

class CommunityData: pass

class UsmUserData:
    authKey = privKey = None
    securityLevel='noAuthNoPriv'
    authProtocol='md5'
    privProtocol='des'
    def __init__(self, securityName, authKey=None, privKey=None):
        self.securityName = securityName
        if authKey is not None:
            self.authKey = authKey
            if self.securityLevel != 'authPriv':
                self.securityLevel = 'authNoPriv'
        if privKey is not None:
            self.privKey = privKey
            self.securityLevel = 'authPriv'

class UdpTransportTarget:
    transportDomain = udp.domainName
    transport = udp.UdpSocketTransport().openClientMode()
    retries = timeout = None  # XXX
    def __init__(self, transportAddr):
        self.transportAddr = (
            socket.gethostbyname(transportAddr[0]), transportAddr[1]
            )

class AsynCmdGen:
    _null = univ.Null()
    def __init__(self, snmpEngine=None):
        if snmpEngine is None:
            self.snmpEngine = engine.SnmpEngine()
        else:
            self.snmpEngine = snmpEngine
        self.mibView = view.MibViewController(
            self.snmpEngine.msgAndPduDsp.mibInstrumController.mibBuilder
            )
        self.__knownAuths = {}
        self.__knownTransports = {}

    def __configure(self, authData, transportTarget):
        paramsName = '%s-params' % (authData.securityName,)
        if not self.__knownAuths.has_key(authData):
            if isinstance(authData, CommunityData):
                # XXX
                config.addV1System(self.snmpEngine, authData.communityName)
            elif isinstance(authData, UsmUserData):
                config.addV3User(
                    self.snmpEngine,
                    authData.securityName,
                    authData.authKey, authData.authProtocol,
                    authData.privKey, authData.privProtocol
                    )
                config.addTargetParams(
                    self.snmpEngine, paramsName,
                    authData.securityName, authData.securityLevel
                    )
            else:
                raise error.PySnmpError('Unsupported SNMP version')
            self.__knownAuths[authData] = 1

        addrName = str(transportTarget.transportAddr)
        if not self.__knownTransports.has_key(transportTarget):
            config.addSocketTransport(
                self.snmpEngine,
                transportTarget.transportDomain,
                transportTarget.transport
                )
            self.__knownTransports[transportTarget] = 1
    
            config.addTargetAddr(
                self.snmpEngine, addrName,
                transportTarget.transportDomain,
                transportTarget.transportAddr,
                paramsName
                )
        return addrName

    # Async SNMP apps
    
    def asyncGetCmd(
        self, authData, transportTarget, varNames, (cbFun, cbCtx)
        ):
        addrName = self.__configure(
            authData, transportTarget
            )
        varBinds = []
        for varName in varNames:
            varBinds.append(
                (mibvar.instanceNameToOid(self.mibView, varName), self._null)
                )
        return cmdgen.GetCmdGen().sendReq(
            self.snmpEngine, addrName, varBinds, cbFun, cbCtx
            )

    def asyncNextCmd(
        self, authData, transportTarget, varNames, (cbFun, cbCtx)
        ):
        addrName = self.__configure(
            authData, transportTarget
            )
        varBinds = []
        for varName in varNames:
            varBinds.append(
                (mibvar.instanceNameToOid(self.mibView, varName), self._null)
                )
        return cmdgen.NextCmdGen().sendReq(
            self.snmpEngine, addrName, varBinds, cbFun, cbCtx
            )

    def asyncBulkCmd(
        self, authData, transportTarget, nonRepeaters, maxRepetitions,
        varNames, (cbFun, cbCtx)
        ):
        addrName = self.__configure(
            authData, transportTarget
            )
        varBinds = []
        for varName in varNames:
            varBinds.append(
                (mibvar.instanceNameToOid(self.mibView, varName), self._null)
                )
        return cmdgen.BulkCmdGen().sendReq(
            self.snmpEngine, addrName, nonRepeaters, maxRepetitions,
            varBinds, cbFun, cbCtx
            )

    def asyncSetCmd(self): pass

class CmdGen(AsynCmdGen):
    def __cbFun(
        self, sendRequestHandle, errorIndication, errorStatus, errorIndex,
        varBinds, cbCtx
        ):
        raise ApplicationReturn(
            errorIndication=errorIndication,
            errorStatus=errorStatus,
            errorIndex=errorIndex,
            varBinds=varBinds
            )
        
    def getCmd(self, authData, transportTarget, *varNames):
        self.asyncGetCmd(
            authData, transportTarget, varNames, (self.__cbFun, None)
            )
        try:
            self.snmpEngine.transportDispatcher.runDispatcher()
        except ApplicationReturn, applicationReturn:
            return (
                applicationReturn['errorIndication'],
                applicationReturn['errorStatus'],
                applicationReturn['errorIndex'],
                applicationReturn['varBinds']
                )

    def nextCmd(self, authData, transportTarget, *varNames):
        def __cbFun(
            sendRequestHandle, errorIndication, errorStatus, errorIndex,
            varBindTable, (varBindHead, varBindTotalTable)
            ):
            if errorIndication or errorStatus:
                raise ApplicationReturn(
                    errorIndication=errorIndication,
                    errorStatus=errorStatus,
                    errorIndex=errorIndex,
                    varBinds=varBindTable[-1],
                    varBindTable=varBindTotalTable
                    )
            else:
                varBindTableRow = varBindTable[-1]
                for idx in range(len(varBindTableRow)):
                    name, val = varBindTableRow[idx]
                    if head[idx].isPrefixOf(name):
                        break
                else:
                    raise ApplicationReturn(
                        errorIndication=errorIndication,
                        errorStatus=errorStatus,
                        errorIndex=errorIndex,
                        varBinds=varBindTable[-1],
                        varBindTable=varBindTotalTable
                        )
                varBindTotalTable.extend(varBindTable)

        head = map(lambda x,self=self: univ.ObjectIdentifier(mibvar.instanceNameToOid(self.mibView, x)), varNames)
                   
        self.asyncNextCmd(
            authData, transportTarget, varNames, (__cbFun, (head, []))
            )
        try:
            while 1:
                self.snmpEngine.transportDispatcher.runDispatcher()
        except ApplicationReturn, applicationReturn:
            return (
                applicationReturn['errorIndication'],
                applicationReturn['errorStatus'],
                applicationReturn['errorIndex'],
                applicationReturn['varBinds'],
                applicationReturn['varBindTable'],
                )

    def bulkCmd(self, authData, transportTarget,
                nonRepeaters, maxRepetitions, *varNames):
        def __cbFun(
            sendRequestHandle, errorIndication, errorStatus, errorIndex,
            varBindTable, (varBindHead, varBindTotalTable)
            ):
            if errorIndication or errorStatus:
                raise ApplicationReturn(
                    errorIndication=errorIndication,
                    errorStatus=errorStatus,
                    errorIndex=errorIndex,
                    varBinds=varBindTable[-1],
                    varBindTable=varBindTotalTable
                    )
            else:
                varBindTotalTable.extend(varBindTable) # XXX out of table 
                                                       # rows possible
                varBindTableRow = varBindTable[-1]
                for idx in range(len(varBindTableRow)):
                    name, val = varBindTableRow[idx]
                    if head[idx].isPrefixOf(name):
                        break
                else:
                    raise ApplicationReturn(
                        errorIndication=errorIndication,
                        errorStatus=errorStatus,
                        errorIndex=errorIndex,
                        varBinds=varBindTable[-1],
                        varBindTable=varBindTotalTable
                        )

        head = map(lambda x,self=self: univ.ObjectIdentifier(mibvar.instanceNameToOid(self.mibView, x)), varNames)
                   
        self.asyncBulkCmd(
            authData, transportTarget, nonRepeaters, maxRepetitions,
            varNames, (__cbFun, (head, []))
            )
        try:
            while 1:
                self.snmpEngine.transportDispatcher.runDispatcher()
        except ApplicationReturn, applicationReturn:
            return (
                applicationReturn['errorIndication'],
                applicationReturn['errorStatus'],
                applicationReturn['errorIndex'],
                applicationReturn['varBinds'],
                applicationReturn['varBindTable']                
                )
    
# XXX
# unify cb params passing
# rename oneliner
# some method for params passing other than exception?
# speed up key localization
# get snmpv1 back to life
# protocol proxy
# traps
# pretty print oid & val at oneliner
