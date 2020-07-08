#    Ryde Player provides a on screen interface and video player for Longmynd compatible tuners.
#    Copyright © 2020 Tim Clark
#
#    This program is free software: you can redistribute it and/or modify
#    it under the terms of the GNU General Public License as published by
#    the Free Software Foundation, either version 3 of the License, or
#    (at your option) any later version.
#
#    This program is distributed in the hope that it will be useful,
#    but WITHOUT ANY WARRANTY; without even the implied warranty of
#    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#    GNU General Public License for more details.
#
#    You should have received a copy of the GNU General Public License
#    along with this program.  If not, see <https://www.gnu.org/licenses/>.

import enum, os, stat, subprocess, pty, select, copy

class inPortEnum(enum.Enum):
    TOP = enum.auto()
    BOTTOM = enum.auto()

class PolarityEnum(enum.Enum):
    NONE = enum.auto()
    HORIZONTAL = enum.auto()
    VERTICAL = enum.auto()

class tunerConfig(object):
    def __init__(self):
        # default is QO-100 Beacon
        self.updateCallback = None # function that is called when the config changes
        self.setConfig(741500, 1500, PolarityEnum.NONE, inPortEnum.TOP)

#    def __init__(self, freq, sr, pol, port):
#        self.freq = freq # frequency
#        self.sr = sr # symbol rate
#        self.pol = pol # LNB polarity / output voltage select, none, horizontal, vertical
#        self.port = port # input port (top/bottom f connector)
#        self.updateCallback = None # function that is called when the config changes

    def setConfig(self, freq, sr, pol, port):
        self.freq = freq
        self.sr = sr
        self.pol = pol
        self.port = port
        self.runCallback()

    def loadConfig(self, config):
        configUpdated = False
        perfectConfig = True
        if not isinstance(config, dict):
            print("Tuner config invalid, skipping")
            perfectConfig = False
        else:
            # check frequency and symbol rate, both must be valid for either to be updated
            if 'freq' in config:
                if isinstance(config['freq'], int):
                    # frequency is valid, check symbol rate
                    if 'sr' in config:
                        if isinstance(config['sr'], int):
                            self.freq = config['freq']
                            self.sr = config['sr']
                            configUpdated = True
                        else:
                            print("Symbol rate config invalid, skipping frequency and symbol rate")
                            perfectConfig = False
                    else:
                        print("Symbol rate missing, skipping frequency and symbol rate")
                        perfectConfig = False
                else:
                    print("Frequency config invalid, skipping frequency and symbol rate")
                    perfectConfig = False
            else:
                print("Frequency config missing, skipping frequency and symbol rate")
                perfectConfig = False

            if 'pol' in config:
                if isinstance(config['pol'], str):
                    polset = False
                    for polopt in PolarityEnum:
                        if polopt.name == config['pol'].upper():
                            self.pol = polopt
                            polset = True
                            configUpdated = True
                            break
                    if not polset:
                        print("Polarity config invalid, skipping")
                        perfectConfig = False
                else:
                    print("Polarity config invalid, skipping")
                    perfectConfig = False
            else:
                print("Polarity config missing, skipping")
                perfectConfig = False

            if 'port' in config:
                if isinstance(config['port'], str):
                    portset = False
                    for portopt in inPortEnum:
                        if portopt.name == config['port'].upper():
                            self.port = portopt
                            polset = True
                            configUpdated = True
                            break
                    if not polset:
                        print("Input port config invalid, skipping")
                        perfectConfig = False
                else:
                    print("Input port config invalid, skipping")
                    perfectConfig = False
            else:
                print("Input port config missing, skipping")
                perfectConfig = False
        if configUpdated: # run the callback if we chaged something
            self.runCallback()
        return perfectConfig

    def setFrequency(self, newFreq):
        self.freq = newFreq
        self.runCallback()
    def setSymbolRate(self, newSr):
        self.sr = newSr
        self.runCallback()
    def setPolarity(self, newPol):
        self.pol = newPol
        self.runCallback()
    def setInputPort(self, newPort):
        self.port = newPort
        self.runCallback()
    def setCallbackFunction(self, newCallback):
        self.updateCallback = newCallback
    def runCallback(self):
        if(self.updateCallback is not None):
            self.updateCallback(self)
    def copyConfig(self):
        # return a copy of the config details but with no callback connected
        newConfig = tunerConfig()
        newConfig.setConfig(self.freq, self.sr, self.pol, self.port)
        return newConfig
    def __eq__(self,other):
        # compare 2 configs ignores the callback
        if not isinstance(other,tunerConfig):
            return NotImplemented
        else:
            return self.freq == other.freq and self.sr == other.sr and self.pol == other.pol and self.port ==other.port
    def __str__(self):
        output = ""
        output += "  Frequency: "+str(self.freq)+"\n"
        output += "Symbol Rate: "+str(self.sr)+"\n"
        output += "   Polarity: "+str(self.pol)+"\n"
        output += "       Port: "+str(self.port)
        return output

class lmManager(object):
    def __init__(self, config, lmpath, mediaFIFOpath, statusFIFOpath):
        # path to the longmynd binary
        self.lmpath = lmpath
        self.mediaFIFOfilename = mediaFIFOpath
        self.statusFIFOfilename = statusFIFOpath
        #TODO: add error handling here
        if(not os.path.exists(self.mediaFIFOfilename)):
            os.mkfifo(self.mediaFIFOfilename)
            print("made")
        elif(not stat.S_ISFIFO(os.stat(self.mediaFIFOfilename).st_mode)):
            print("media pipe is not a fifo")
        if(not os.path.exists(self.statusFIFOfilename)):
            os.mkfifo(self.statusFIFOfilename)
        elif(not stat.S_ISFIFO(os.stat(self.statusFIFOfilename).st_mode)):
            print("status pipe is not a fifo")
        self.vlcMediaFd =os.fdopen( os.open(self.mediaFIFOfilename, flags=os.O_NONBLOCK, mode=os.O_RDONLY)) # an open file descriptor to pass to vlc (or another player)
#        self.vlcMediaFd = None
        self.statusFIFOfd = os.fdopen(os.open(self.statusFIFOfilename, flags=os.O_NONBLOCK, mode=os.O_RDONLY)) # the status fifo file descriptor
        rpipe, self.stdoutWritefd = pty.openpty() # a pty for interacting with longmynds STDOUT, couldn't get pipes to work
        self.stdoutReadfd = os.fdopen(rpipe, 'r')
        self.process = None
        self.statelog = [] # log of important things from longmynds STDOUT
        self.lmlog = [] # a complete longmmynd output log, for debugging
        self.lmstarted = False
        self.statusrecv = False
        self.activeConfig = config.copyConfig()
        self.pidCacheWait = True
        self.pidCacheFault = False
        self.pidCache = {}
        self.pidCachePair = (None,None)
        self.lastState = { 'state':None, 'provider': '', 'service': '', 'modcode': None, 'pids': {} }
        self.changeRefState = copy.deepcopy(self.lastState)
        self.stateMonotonic = 0

    def reconfig(self, config):
        """reconfigures longmynd"""
        if(isinstance(config, tunerConfig) and config != self.activeConfig):
            self.activeConfig = config.copyConfig()
            print(self.activeConfig)
            self.restart()
    def remedia(self):
        self.vlcMediaFd.close()
        self.vlcMediaFd =os.fdopen( os.open(self.mediaFIFOfilename, flags=os.O_NONBLOCK, mode=os.O_RDONLY)) # an open file descriptor to pass to vlc (or another player)
    def getMediaFd(self):
        return self.vlcMediaFd
    def getFDs(self):
        return [self.statusFIFOfd, self.stdoutReadfd]
    def handleFD(self, fd):
        """handles a file descriptor that has data to read"""
        fdCallbacks = dict()
        fdCallbacks[self.statusFIFOfd] = self.processStatus
        fdCallbacks[self.stdoutReadfd] = self.processStdout
        if(fd in fdCallbacks):
            fdCallbacks[fd]()
    def isRunning(self):
        if(self.process != None and self.lmstarted and self.statusrecv):
            polled = self.process.poll()
            return polled==None
        else:
            return False
    def isLocked(self):
        """returns if longmynd is locked on to a signal
        This does not mean it can be decoded, MER may still be too high
        """
        if(self.isRunning()):
            print("state:"+str(self.lastState))
            if(self.lastState['state'] in [3,4]):
                return True
            else:
                return False
        else:
            return False
    def getMonotonicState(self):
        return self.stateMonotonic
    
    def processStatus(self):
        """process the status FIFO data"""
        #TODO: handle more of the status messages
        lines =  self.statusFIFOfd.readlines()
        for line in lines:
            self.statusrecv = True
            if(line[0] == '$'):
                rawtype,rawval = line[1:].rstrip().split(',',1)
                msgtype = int(rawtype)
                if msgtype == 1: # State
                    if self.lastState != self.changeRefState : # if the signal parameters have changed
                        self.stateMonotonic += 1
                    self.lastState['state'] = int(rawval)
                    if int(rawval) < 3: # if it nis ot locked, reset some state
                        self.lastState['provider'] = ""
                        self.lastState['service'] = ""
                        self.lastState['modcode'] = None
                        self.lastState['pids'] = {}
                    if self.lastState != self.changeRefState : # if the signal parameters have changed
                        self.stateMonotonic = 0
                elif msgtype == 13:
                    self.lastState['provider'] = rawval
                elif msgtype == 14:
                    self.lastState['service'] = rawval
                elif msgtype == 18:
                    self.lastState['modcode'] = int(rawval)

                # PID list accumulator
                if msgtype == 16: # ES PID
                    self.pidCacheWait = False
                    if self.pidCachePair[0] == None:
                        self.pidCachePair = (int(rawval), self.pidCachePair[1])
                        if self.pidCachePair[1] != None:
                            self.pidCache[pidCachePair[0]] = pidCachePair[1]
                            self.pidCachePair = (None, None)
                    else:
                        self.pidCacheFault = True
                        print("pid cache fault")
                elif msgtype == 17: # ES Type
                    self.pidCacheWait = False
                    if self.pidCachePair[1] == None:
                        self.pidCachePair = (self.pidCachePair[0], int(rawval))
                        if self.pidCachePair[0] != None:
                            self.pidCache[self.pidCachePair[0]] = self.pidCachePair[1]
                            self.pidCachePair = (None, None)
                    else:
                        self.pidCacheFault = True
                        print("pid cache fault")
                # update pid status once we have them all (uness there was a fault)
                elif not self.pidCacheWait:
                    if not self.pidCacheFault:
                        self.lastState['pids'] = self.pidCache
                    self.pidCacheFault = False
                    self.pidCacheWait = True
                    self.pidCache = {}
                    self.pidCachePair= (None,None)
                if(msgtype in [1, 6, 9, 12]):
                    print(str(msgtype)+":"+rawval)

    def processStdout(self):
        """track the starup state of longmynd from its STDOUT"""
        rawnewline = self.stdoutReadfd.readline()
        newline = rawnewline.rstrip()
        self.lmlog.append(newline)
        if(newline.startswith("ERROR:")):
            # its probably crashed, stop and output
            self.stop(True, True)
        if(newline.lstrip().startswith("Status:")):
            self.statelog.append(newline.lstrip()[8:])
            fifosopen = 0
            usbopen = False
            stvopen = False
            tuneropen = False
            lnasfound = 0
            for line in self.statelog:
                if(line == 'opened fifo ok'):
                    fifosopen += 1
                elif(line.startswith('MPSSE')):
                    usbopen = True
                elif(line.startswith('STV0910 MID')):
                    stvopen = True
                elif(line.startswith('tuner:')):
                    tuneropen = True
                elif(line == 'found new NIM with LNAs'):
                    lnasfound += 1

            if(fifosopen==2 and usbopen and stvopen and tuneropen and lnasfound==2):
                self.lmstarted = True
                print("lm started")

    def stop(self, dumpOutput = False, waitfirst=False):
        #waitfirst is for if its crashed and we want to wait for it to die on its own so we get all the output
        if(waitfirst):
            try:
                self.process.communicate(timeout=4)
            except subprocess.TimeoutExpired:
                dumpOutput=True
                self.process.terminate()
                self.process.communicate()
        else:
            self.process.terminate()
            self.process.communicate()
        os.close(self.stdoutWritefd)
        #Drain the stdout buffer
        while True:
            try:
                rawnewline = self.stdoutReadfd.readline()
            except IOError as e:
                break
            newline = rawnewline.rstrip()
            self.lmlog.append(newline)
        self.statusFIFOfd.close()
        #open a clean buffer ready for the restart

        self.statusFIFOfd = os.fdopen(os.open(self.statusFIFOfilename, flags=os.O_NONBLOCK, mode=os.O_RDONLY))
        rpipe, self.stdoutWritefd = pty.openpty()
        self.stdoutReadfd = os.fdopen(rpipe, 'r')
        self.process = None
        #TODO: parse this and display a meaningful message on screen
        if dumpOutput:
            for logline in self.lmlog:
                print(logline)
    def start(self):
        if(self.process == None):
            print("start")
            self.lmstarted = False
            self.statusrecv = False
            self.statelog=[]
            self.lmlog=[]
            args = [self.lmpath, '-t', self.mediaFIFOfilename, '-s', self.statusFIFOfilename]
            if self.activeConfig.port == inPortEnum.BOTTOM:
                args.append('-w')
            if self.activeConfig.pol == PolarityEnum.HORIZONTAL:
                args.extend(['-p', 'h'])
            elif self.activeConfig.pol == PolarityEnum.VERTICAL:
                args.extend(['-p', 'v'])
            args.append(str(self.activeConfig.freq))
            args.append(str(self.activeConfig.sr))
            self.process = subprocess.Popen(args, stdout=self.stdoutWritefd, stderr=subprocess.STDOUT, bufsize=0)
        else:
            print("LM already running")
    def restart(self):
        if(self.process != None):
            self.stop()
        self.start()
