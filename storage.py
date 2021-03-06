#!/usr/bin/env python
# -*- coding: utf-8 -*-
# @Time    : 2019/8/27 14:49
# @Version : 1.0
# @File    : storagev1_2.py
# @Author  : Jingsheng Tang
# @Version : 1.0
# @Contact : mrtang@nudt.edu.cn   mrtang_cs@163.com
# @License : (C) All Rights Reserved

import os
from copy import copy
import json
import time
from multiprocessing import Queue,Event
import multiprocessing
from signal_generator import EEGamp
import numpy as np

# storage 通过tcp/ip监听指令决定是否启动数据保存以及结束保存
# 任何时候其他进程都可发起数据保存请求，但是否被保存取决于当前是否处于保存数据的状态
# 这样，可以保障信号处理主进程可以始终运行，而数据保存仅在需要保存数据时保存数据

class Storage():
    def __init__(self,configs):
        self.configs = configs
        self.OK = False

    def prefile(self):
        # 生成文件名
        head = self.configs['subject'] + '-S%02iR' % (self.configs['session'])  #eg. sub-S01R
        filename = head + '001'
        newfilename = self.configs['storage path'] + '//' + filename
        if not os.path.exists(self.configs['storage path']): # 没有目录则创建目录
            os.makedirs(self.configs['storage path'])
        else:
            files = os.listdir(self.configs['storage path']) # 遍历所有文件
            nums = [self.getnum(f, head) for f in files if self.getnum(f, head) > -1]
            if nums != []:    newfilename = self.configs['storage path'] + '//' + head + '%03i' % (max(nums) + 1)

        self.marker_file_name = newfilename + ".npz"
        self.eeg_file_name = newfilename + ".eeg"

        # 构造文件头信息
        temconfig = copy(self.configs)
        temconfig['marker_file_name'] = os.path.split(self.marker_file_name)[-1]
        temconfig['eeg_file_name'] = os.path.split(self.eeg_file_name)[-1]
        temconfig['description'] = 'BCIROS platform developed by mrtang'
        temconfig['eegdatafile format'] = 'float32'

        del temconfig['sp_host_ip']
        del temconfig['sp_host_port']
        del temconfig['result receiver address']

        header_string = json.dumps(temconfig) + '\n'
        header_string = bytes(header_string,encoding='utf-8')

        # 写入eeg文件头部
        with open(self.eeg_file_name, 'wb') as f:
            f.write(header_string)

        self._markers_ = {'HeaderInfo':header_string}
        self.mkrCLK = time.clock()
        self.eeg_str = b''
        self.eegCLK = time.clock()
        print('[storage module] ready:\n>>>> eegfile: %s \n>>>> mkrfile: %s'%(self.eeg_file_name,self.marker_file_name))

        self.OK = True

    def getnum(self,file,head):
        hi = file.find(head)
        if hi==-1:  # 没有搜索到特征命名
            return -1
        else:       # 搜索到特征命名
            try:
                filename,_ = os.path.splitext(file)
                num = int(filename[hi+len(head):])
                return num
            except:
                return -1

    def write_eeg_string(self,eegstr): # eeg: ch1point1,ch2point1,ch3point1,ch1point2,ch2point2..., np.float32
        if not self.OK: return
        self.eeg_str += eegstr
        nCLK = time.clock()
        if nCLK - self.eegCLK > 5:  # 5秒左右保存一次数据
            with open(self.eeg_file_name,'ab') as f:
                try:
                    f.write(self.eeg_str)
                    f.flush()
                except:
                    print('wrong')
                self.eeg_str = b''
            self.eegCLK = nCLK

    def write_eeg(self,eeg):
        '''
        eeg: ch x N
        to dat file: ch1point1 ch2point1 ch1point2 ch2point2
        '''
        if not self.OK: return
        eegstr = eeg.transpose().flatten().astype(np.float32).tostring()
        self.write_eeg_string(eegstr)

    def write_marker(self,marker): # 允许动态添加marker
        # 读取npz文件方法
        '''
        the method to read the marker file:
        eg. the markers contains two marker: 'mkr1','mkr2'
        file = np.load(marker_file_name,allow_pickle = True)
        markers = file['markers'][()]
        mkr1 = markers['mkr1']
        mkr2 = markers['mkr2']
        info = markers['HeaderInfo']
        '''

        # marker: eg. {'mkr1':{'value':[0],'timepoint':[100]},'mkr2':{'value':[1,2],'timepoint':[200,250]}}
        # 更新marker

        if not self.OK:  return
        for key in marker:
            mkr = marker[key]
            mk = np.array([mkr['value'],mkr['timepoint']])
            if key in self._markers_:
                self._markers_[key] = np.hstack([self._markers_[key],mk])
            else:
                self._markers_[key] = mk

        nCLK = time.clock()
        if nCLK - self.mkrCLK > 3:  # 不少于3秒保存一次数据
            self.mkrCLK = nCLK
            np.savez(self.marker_file_name,markers = self._markers_)

# 一般情况为storage模块开启一个独立进程
class StorageInterface():
    def __init__(self):
        self.ok = Event()
        self.ev = Event()
        self.que = Queue()
        self.args = {'ev':self.ev,'que':self.que,'ok':self.ok}

    def write_eeg_to_file(self,eeg):
        '''
        eeg:  ch x N
        '''
        if eeg is not None:
            self.que.put(['eeg',eeg])
            
    def new_record(self):
        self.que.put(['new-record',''])
    
    def end_record(self):
        self.que.put(['end-record',''])

    def write_mkr_to_file(self,mkr):
        if len(mkr)>0:
            self.que.put(['mkr',mkr])

    def close(self):
        self.que.put(['end',''])
        self.ev.set()

    def wait(self):
        self.ok.wait()

    def __del__(self):
        self.que.put(['end', ''])
        self.ev.set()

def storage_pro(args,configs):
    ok = args['ok']
    ev = args['ev']
    que = args['que']
    st = Storage(configs)
    # st.prefile()
    ok.set() #发送初始化完成的状态

    while not ev.is_set():
        flg,buf = que.get()
        if flg == 'eeg':
            st.write_eeg(buf)
        elif flg == 'mkr':
            st.write_marker(buf)
        elif flg == 'new-record':       #创建一个新文件
            st.prefile()
        elif flg == 'end-record':
            st.OK = False               # 停止当前记录
        else:
            pass
    print('[storage module] process exit')

def main():
    with open(r'./config.js', 'r') as f:
        config = json.load(f)
    config = config['signal_processing']

    eegamp = EEGamp(100, config['eeg channels'])
    stIF = StorageInterface()
    p = multiprocessing.Process(target=storage_pro, args=(stIF.args, config))
    p.start()
    stIF.wait()

    for i in range(100):
        # marker = {'mkr':{'value':[i,i],'timepoint':[i*10,i*10]}}
        # stIF.write_mkr_to_file(marker)
        data = eegamp.read()
        stIF.write_eeg_to_file(data)
        time.sleep(0.1)
    stIF.close()

def read_mkr(filename):
    file = np.load(filename, allow_pickle=True)
    markers = file['markers'][()]
    mkr_info = json.loads(markers['HeaderInfo'])
    del markers['HeaderInfo']
    return mkr_info,markers

def read_eeg(filename):
    with open(filename,'rb') as f:
        info_buf = f.readline()
        info = bytes.decode(info_buf,encoding='utf-8')
        info = json.loads(info)
        data = np.fromstring(f.read(),dtype = np.float32)
    ll = data.shape[-1]
    chs = len(info['eeg channels'])
    eeg = data.reshape(int(ll/chs),chs).transpose()
    return info,eeg


if __name__ == '__main__':
    # read_mkr('f:\\data\\Test-S01R001.npz')
    # main()

    # info,eeg = read_eeg('f:\\data\\Test-S01R001.eeg')
    # info1,mkr = read_mkr('f:\\data\\Test-S01R001.npz')
    main()
    pass




