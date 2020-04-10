import rpyc
import sys
import threading
import random
import sqlite3
from sqlite3 import Error
from threading import Timer
import os
import time

class Node(rpyc.Service):

    def __init__(self, argNodeIdx, argNodeHost, argNodePort):
        self.middlewareHost = "localhost"
        self.middlewarePort = 5000
        self.curNodeHost = argNodeHost
        self.curNodePort = argNodePort
        self.curNodeIdx = int(argNodeIdx)

        self.allNodesHost, self.allNodesPort = self.getAllMembers()
        self.numNodes = len(self.allNodesHost)
        print(self.allNodesHost)
        print(self.allNodesPort)

        self.joinGroup()
        print(self.allNodesHost)
        print(self.allNodesPort)

        # sqlite connection
        self.conn = None
        try:
            self.conn = sqlite3.connect("pythonsqlite.db")
            print(sqlite3.version)
        except Error as e:
            print(e)
        if self.conn == None:
            print("connection to db failed")

        # initial status setup
        self.currentState = "follower"
        self.currentTerm = 0
        self.votedFor = None
        self.currentLeader = None
        self.totalVotesCount = 0
        self.leaderTimer = None
        self.followerTimer = None
        self.electionTimer = None
        self.votesCheckTimer = None

        mainThread = threading.Thread(target = self.run_server)
        mainThread.start()

    def run_server(self):
        while True:
            if self.currentState == 'follower':
                if self.followerTimer == None:
                    self.followerTimer = Timer(random.randint(20000, 30000)/float(10000), self.BecomeCandidate)
                    self.followerTimer.start()
            elif self.currentState == 'candidate':
                if self.electionTimer == None:
                    self.electionTimer = Timer(random.randint(10000, 20000)/float(10000), self.setupElection)
                    self.electionTimer.start()
                if self.votesCheckTimer == None:
                    self.votesCheckTimer = Timer(.2, self.candidateCheckVotes)
                    self.votesCheckTimer.start()
            elif self.currentState == 'leader':
                if self.leaderTimer == None:
                    self.leaderTimer = Timer(1.0, self.leaderAction)
                    self.leaderTimer.start()

    '''
        1.1 Code related to scenario when follower state ends
    '''
    def BecomeCandidate(self):
        print('follwer','node', self.curNodeIdx, 'term', self.currentTerm)
        print('follower','state', self.currentState)
        self.currentState = 'candidate'
        self.setupElection()

    '''
        1.1 ends
    '''

    '''
        1.2 Code related to scenario when current node is in candidate state
    '''

    def setupElection(self):
        self.currentTerm += 1
        self.totalVotesCount = 0
        self.totalVotesCount += 1
        self.votedFor = self.curNodeIdx

        if self.electionTimer != None:
            self.electionTimer.cancel()
            self.electionTimer = None
        if self.votesCheckTimer != None:
            self.votesCheckTimer.cancel()
            self.votesCheckTimer = None

        for nodeIdx in self.allNodesHost:
            if nodeIdx == self.curNodeIdx:
                continue
            args = (self.currentTerm, self.curNodeIdx, self.allNodesHost[nodeIdx], self.allNodesPort[nodeIdx])
            t = threading.Thread(target = self.startElection, args= args)
            t.start()

    def startElection(self, currentTerm, curNode, host, portNum):
        try:
            conn = rpyc.connect(host, portNum)
            vote = conn.root.requestVote(currentTerm, curNode)
            if vote == True:
                self.totalVotesCount += 1
        except Exception:
            print("Node", portNum, "crashed")

    def exposed_requestVote(self, term, candidateID):
        if term > self.currentTerm:
            self.voteForThisCandidate(term, candidateID)
            return True
        elif term == self.currentTerm:
            if self.votedFor == None:
                self.voteForThisCandidate(term, candidateID)
                return True
            else:
                return False
        elif term < self.currentTerm:
            return False

    def voteForThisCandidate(self, term, candidateID):
        self.totalVotesCount = 0
        self.currentState = 'follower'
        if self.followerTimer != None:
            self.followerTimer.cancel()
            self.followerTimer = None
        self.currentTerm = term
        self.votedFor = candidateID

    def candidateCheckVotes(self):
        if self.votesCheckTimer != None:
            self.votesCheckTimer.cancel()
            self.votesCheckTimer = None
        print('node', self.curNodeIdx, 'term', self.currentTerm)
        print('state', self.currentState)
        print(self.totalVotesCount)
        if self.totalVotesCount > self.numNodes/2:
            if self.electionTimer != None:
                self.electionTimer.cancel()
                self.electionTimer = None
            self.currentState = 'leader'
            self.currentLeader = self.curNodeIdx
            self.leaderAction()
    '''
        1.2 ends
    '''

    '''
        1.3 Code related to scenario when current node is leader
    '''
    def leaderAction(self):
        if self.leaderTimer != None:
            self.leaderTimer.cancel()
            self.leaderTimer = None
        for nodeIdx in self.allNodesHost:
            if nodeIdx == self.curNodeIdx:
                continue
            args = (self.currentTerm, self.curNodeIdx, self.allNodesHost[nodeIdx], self.allNodesPort[nodeIdx])
            t = threading.Thread(target = self.sendHeartBeat, args= args)
            t.start()
        #todo group management: update nginx leader ip

    def sendHeartBeat(self, currentTerm, leaderID, host, portNum):
        try:
            conn = rpyc.connect(host, portNum)
            respSuccess, respTerm = conn.root.maitainFollowerState(currentTerm, leaderID)
            print('Sent HeartBeat to', portNum)
            if respSuccess == False:
                self.currentState = 'follower'
                self.currentTerm = respTerm
                self.votedFor = None
        except Exception:
            print("Node", portNum, "crashed at send heart beat")

    def exposed_maitainFollowerState(self, term, leaderID):
        if term > self.currentTerm:
            self.followerTimer.cancel()
            self.followerTimer = None
            self.currentTerm = term
            self.currentState = 'follower'
            self.currentLeader = leaderID
            self.votedFor = None
            return (True, self.currentTerm)
        elif term == self.currentTerm:
            self.followerTimer.cancel()
            self.followerTimer = None
            self.currentState = 'follower'
            self.currentLeader = leaderID
            return (True, self.currentTerm)
        else:
            return (False, self.currentTerm)
    '''
        1.3 ends
    '''

    '''
        1.4 Code related to group management
    '''

    def exposed_addMember(self, newMemIdx, newMemHost, newMemPort):
        self.allNodesHost[newMemIdx] = newMemHost
        self.allNodesPort[newMemIdx] = newMemPort
        self.numNodes = len(self.allNodesHost)

    '''
    def exposed_removeMember(self, oldNodeHost, oldNodePort):
        self.allNodesHost.remove(oldNodeHost)
        self.allNodesPort.remove(oldNodePort)
    '''

    def joinGroup(self):
        try:
            conn = rpyc.connect(self.middlewareHost, self.middlewarePort)
            conn.root.addNode(self.curNodeIdx, self.curNodeHost, self.curNodePort)
        except Exception:
            print("Node", self.curNodeIdx, "failed joining group")
        self.allNodesHost[self.curNodeIdx] = self.curNodeHost
        self.allNodesPort[self.curNodeIdx] = self.curNodePort
        self.numNodes = len(self.allNodesHost)

    def getAllMembers(self):
        try:
            conn = rpyc.connect(self.middlewareHost, self.middlewarePort)
            return conn.root.getNodeList()
        except Exception:
            print("Middleware", portNum, "failed returning all members")

    def exposed_is_leader(self):
        return self.currentState == 'leader'

if __name__ == '__main__':
    from rpyc.utils.server import ThreadPoolServer
    nodeIdx = sys.argv[1]
    host = sys.argv[2]
    port = sys.argv[3]
    server = ThreadPoolServer(Node(nodeIdx, host, port), port = int(port))
    server.start()
