from common.phx_protocol import *
from common.phx_structs import *
from common.phx_definitions import *
from common.phx_trader_spi import CPhxFtdcTraderSpi
from common.phx_trader_api import CPhxFtdcTraderApi
from test.OrderManager import OrderManager
import time
import json
from collections import deque
from optparse import OptionParser
from random import randint, random
from test.OrderList import OrderList, OrderInfo, Snapshot
import threading
import copy
import pandas as pd
from sqlalchemy import create_engine


class MyClient(CPhxFtdcTraderSpi):
    def __init__(self):
        super().__init__()
        self.serverHost = '127.0.0.1'
        self.serverOrderPort = 0
        self.serverQryPort = 0
        self.serverRtnPort = 0
        self.serverMDPort = 0
        self.nRequestID = 0
        self.orderRef = 0
        self.m_Token = ''
        self.m_UserID = None
        self.m_Passwd = '123456'
        self.m_LoginStatus = [False, False, False, False]
        self.query_status = False
        self.is_any_updated = False
        self.game_status = None
        self.ins2om = {}
        self.ins2index = {}
        self.instruments = []
        self.md_list = []  # array of md deque
        self.inst_num = 0
        self.market_data_updated = []
        self._background = None
        self.m_pUserApi = CPhxFtdcTraderApi()

        self.options_prices=[]
        self.options_names = []

        self.market_bid_offer=[]
        self.market_ask_offer=[]
        self.market_ops=[]

        self.in_sql=True

        self.market_engine = create_engine('mysql+pymysql://root:chj5chj5@localhost/jiukun?charset=utf8')
        self.account_engine = create_engine('mysql+pymysql://root:chj5chj5@localhost/jiukun?charset=utf8')

    def reset(self):
        """Reset function after each round"""
        for ins, om in self.ins2om.items():
            om.clear()
            self.md_list[self.ins2index[ins]].clear()
            self.market_data_updated[self.ins2index[ins]] = False

        self.is_any_updated = False

    def next_request_id(self):
        self.nRequestID += 1
        return self.nRequestID

    def next_order_ref(self):
        self.orderRef += 1
        return self.orderRef

    def OnFrontConnected(self):
        print("OnFrontConnected, Start to ReqUserLogin")
        self.ReqUserLogin()

    def ReqUserLogin(self):
        field = CPhxFtdcReqUserLoginField()
        field.UserID = self.m_UserID
        field.Password = self.m_Passwd
        ret = self.m_pUserApi.ReqUserLogin(field, PHX_LINK_TYPE_Order, self.next_request_id())
        print("ReqUserLogin Order (%s:%d) ret=%d" % (self.serverHost, self.serverOrderPort, ret))
        ret = self.m_pUserApi.ReqUserLogin(field, PHX_LINK_TYPE_Qry, self.next_request_id())
        print("ReqUserLogin Qry (%s:%d) ret=%d" % (self.serverHost, self.serverQryPort, ret))
        ret = self.m_pUserApi.ReqUserLogin(field, PHX_LINK_TYPE_Rtn, self.next_request_id())
        print("ReqUserLogin Rtn (%s:%d) ret=%d" % (self.serverHost, self.serverRtnPort, ret))
        ret = self.m_pUserApi.ReqUserLogin(field, PHX_LINK_TYPE_MD, self.next_request_id())
        print("ReqUserLogin MD (%s:%d) ret=%d" % (self.serverHost, self.serverMDPort, ret))

    def OnRspUserLogin(self, pRspUserLogin: CPhxFtdcRspUserLoginField, LinkType, ErrorID, nRequestID):
        print('OnRspUserLogin, data=%s, ErrorID=%d, ErrMsg=%s, nRequestID=%d' % (json.dumps(pRspUserLogin.__dict__), ErrorID, get_server_error(ErrorID), nRequestID))
        if ErrorID == 0:
            self.m_LoginStatus[LinkType] = True
            if pRspUserLogin.MaxOrderLocalID > self.orderRef:
                self.orderRef = pRspUserLogin.MaxOrderLocalID + 1

    def OnRspOrderInsert(self, pInputOrder: CPhxFtdcInputOrderField, ErrorID):
        if ErrorID != 0:
            print('OnRspOrderInsert, orderRef=%d, ErrorID=%d, ErrMsg=%s' % (pInputOrder.OrderLocalID, ErrorID, get_server_error(ErrorID)))
            if pInputOrder.InstrumentID not in self.ins2om:
                return
            om = self.ins2om[pInputOrder.InstrumentID]
            om.on_rsp_order_insert(pInputOrder.OrderLocalID)

    def OnRspOrderAction(self, pInputOrderAction: CPhxFtdcOrderActionField, ErrorID):
        if ErrorID != 0:
            print('OnRspOrderAction, orderRef=%d, ErrorID=%d, ErrMsg=%s' % (pInputOrderAction.OrderLocalID, ErrorID, get_server_error(ErrorID)))

    def OnRspQryTradingAccount(self, pTradingAccount: CPhxFtdcRspClientAccountField, ErrorID, nRequestID, bIsLast):
        if self.in_sql:
            try:
                df = pd.DataFrame({time.time(): pTradingAccount.__dict__}).T
                df.to_sql('account', self.account_engine , index=True, if_exists='append')
            except:
                pass
        print('OnRspQryTradingAccount, data=%s, ErrorID=%d, ErrMsg=%s, bIsLast=%d' % (json.dumps(pTradingAccount.__dict__), ErrorID, get_server_error(ErrorID), bIsLast))

    def OnRspQryInstrument(self, pInstrument: CPhxFtdcRspInstrumentField, ErrorID, nRequestID, bIsLast):
        # print('OnRspQryInstrument, data=%s, ErrorID=%d, bIsLast=%d' % (json.dumps(pInstrument.__dict__), ErrorID, bIsLast))
        if pInstrument.InstrumentID not in self.ins2om:
            self.ins2om[pInstrument.InstrumentID] = OrderManager(pInstrument.InstrumentID)
            self.md_list.append(deque(maxlen=10))
            self.instruments.append(copy.copy(pInstrument))
            self.market_data_updated.append(False)
            self.ins2index[pInstrument.InstrumentID] = self.inst_num
            self.inst_num += 1

        if bIsLast:
            self.query_status = True
            print("total %d instruments" % self.inst_num)

    def OnRtnGameStatus(self, pGameStatus: CPhxFtdcGameStatusField):
        # print('OnRtnGameStatus, data=%s' % json.dumps(pGameStatus.__dict__))
        self.game_status = pGameStatus

    def OnRtnMarketData(self, pMarketData: CPhxFtdcDepthMarketDataField):
        if pMarketData.InstrumentID in self.ins2index:
            # print('OnRtnMarketData, data=%s' % json.dumps(pMarketData.__dict__))
            # data=pMarketData.__dict__
            if self.in_sql:
                try:
                    # self.market_engine = create_engine('mysql+pymysql://root:chj5chj5@localhost/jiukun?charset=utf8')
                    df = pd.DataFrame({time.time():pMarketData.__dict__}).T
                    df.to_sql('jiukun', self.market_engine, index=True, if_exists='append')
                except:
                    pass

            index = self.ins2index[pMarketData.InstrumentID]
            self.md_list[index].append(pMarketData)
            self.market_data_updated[index] = True
            self.is_any_updated = True

    def OnRtnOrder(self, pOrder: CPhxFtdcOrderField):
        if pOrder.InstrumentID not in self.ins2om:
            return
        om = self.ins2om[pOrder.InstrumentID]
        om.on_rtn_order(pOrder)

    def OnRtnTrade(self, pTrade: CPhxFtdcTradeField):
        # print('OnRtnTrade, data=%s' % json.dumps(pTrade.__dict__))
        if pTrade.InstrumentID not in self.ins2om:
            return
        om = self.ins2om[pTrade.InstrumentID]
        om.on_rtn_trade(pTrade)

    def OnErrRtnOrderInsert(self, pInputOrder: CPhxFtdcInputOrderField, ErrorID):
        if ErrorID != 0:
            print('OnErrRtnOrderInsert, orderRef=%d, ErrorID=%d, ErrMsg=%s' % (pInputOrder.OrderLocalID, pInputOrder.ExchangeErrorID, get_server_error(pInputOrder.ExchangeErrorID)))
            if pInputOrder.InstrumentID not in self.ins2om:
                return
            om = self.ins2om[pInputOrder.InstrumentID]
            om.on_rsp_order_insert(pInputOrder.OrderLocalID)

    def OnErrRtnOrderAction(self, pOrderAction: CPhxFtdcOrderActionField, ErrorID):
        if ErrorID != 0:
            print('OnErrRtnOrderAction, orderRef=%d, ErrorID=%d, ErrMsg=%s' % (pOrderAction.OrderLocalID, pOrderAction.ExchangeErrorID, get_server_error(pOrderAction.ExchangeErrorID)))

    def OnRspQryOrder(self, pOrder: CPhxFtdcOrderField, ErrorID, nRequestID, bIsLast):
        if pOrder is not None and ErrorID == 0:
            if pOrder.InstrumentID not in self.ins2om:
                return
            om = self.ins2om[pOrder.InstrumentID]
            om.insert_init_order(pOrder)
            om.on_rtn_order(pOrder)

        if bIsLast:
            self.query_status = True
            print("init order query over")

    def OnRspQryTrade(self, pTrade: CPhxFtdcTradeField, ErrorID, nRequestID, bIsLast):
        if pTrade is not None and ErrorID == 0:
            if pTrade.InstrumentID not in self.ins2om:
                return
            om = self.ins2om[pTrade.InstrumentID]
            om.on_rtn_trade(pTrade)

        if bIsLast:
            self.query_status = True
            print("init trade query over")

    def timeout_wait(self, timeout, condition=None):
        while timeout > 0:
            time.sleep(1)
            timeout -= 1
            if condition is None:
                if self.query_status:
                    return True
            elif isinstance(condition, list):
                if all(condition):
                    return True
        return False

    def Init(self):
        self.m_pUserApi.RegisterSpi(self)
        self.m_pUserApi.RegisterOrderFront(self.serverHost, self.serverOrderPort)
        self.m_pUserApi.RegisterQryFront(self.serverHost, self.serverQryPort)
        self.m_pUserApi.RegisterRtnFront(self.serverHost, self.serverRtnPort)
        self.m_pUserApi.RegisterMDFront(self.serverHost, self.serverMDPort)

        self.m_pUserApi.Init()
        if not self.timeout_wait(10, self.m_LoginStatus):
            return False

        print("OnRspUserLogin, all link ready")
        self.query_status = False
        ret = self.m_pUserApi.ReqQryInstrument(CPhxFtdcQryInstrumentField(), self.next_request_id())
        if (not ret) or (not self.timeout_wait(10)):
            print("ReqQryInstrument failed")
            return False

        self.query_status = False
        field = CPhxFtdcQryOrderField()
        field.InvestorID = self.m_UserID
        ret = self.m_pUserApi.ReqQryOrder(field, self.next_request_id())
        if (not ret) or (not self.timeout_wait(10)):
            print("ReqQryOrder failed")
            return False

        self.query_status = False
        field = CPhxFtdcQryTradeField()
        field.InvestorID = self.m_UserID
        ret = self.m_pUserApi.ReqQryTrade(field, self.next_request_id())
        if (not ret) or (not self.timeout_wait(10)):
            print("ReqQryTrade failed")
            return False

        self._background = threading.Thread(target=self.background_thread)
        self._background.start()

        if not self.timeout_wait(10):
            return False

        self.get_price_list()
        return True

    def background_thread(self):
        print("start background thread")
        last_time = time.time()
        field = CPhxFtdcQryClientAccountField()
        while True:
            t = time.time()
            if t - last_time > 5 and self.m_pUserApi.all_connected:
                last_time = t
                ret = self.m_pUserApi.ReqQryTradingAccount(field, self.next_request_id())
                if not ret:
                    print("ReqQryTradingAccount failed")
            time.sleep(1)

    def random_direction(self):
        if randint(0, 1) == 0:
            return PHX_FTDC_D_Buy
        else:
            return PHX_FTDC_D_Sell

    def random_offset(self):
        if randint(0, 1) == 0:
            return PHX_FTDC_OF_Open
        else:
            return PHX_FTDC_OF_Close

    def send_input_order(self, order: OrderInfo):
        field = CPhxFtdcQuickInputOrderField()
        field.OrderPriceType = order.OrderPriceType
        field.OffsetFlag = order.OffsetFlag
        field.HedgeFlag = PHX_FTDC_HF_Speculation
        field.InstrumentID = order.InstrumentID
        field.Direction = order.Direction
        field.VolumeTotalOriginal = order.VolumeTotalOriginal
        field.TimeCondition = PHX_FTDC_TC_GFD
        field.VolumeCondition = PHX_FTDC_VC_AV
        if order.OrderPriceType == PHX_FTDC_OPT_LimitPrice:
            field.LimitPrice = order.LimitPrice
        field.OrderLocalID = order.OrderLocalID
        ret = self.m_pUserApi.ReqQuickOrderInsert(field, self.next_request_id())
        # print(order.OrderLocalID)
        # print("QuickOrderInsert ", field, ret)

    def send_cancel_order(self, order: OrderInfo):
        field = CPhxFtdcOrderActionField()
        field.OrderSysID = order.OrderSysID
        field.InvestorID = self.m_UserID
        field.OrderLocalID = order.OrderLocalID
        ret = self.m_pUserApi.ReqOrderAction(field, self.next_request_id())
        # print("ActionOrder data=%s, ret=%d" % (json.dumps(field.__dict__), ret))

    def random_input_order(self, ins_idx):
        ins = self.instruments[ins_idx]
        om = self.ins2om[ins.InstrumentID]
        order = om.place_limit_order(self.next_order_ref(), self.random_direction(), self.random_offset(), random() * 20, 1)
        self.send_input_order(order)

    def random_cancel_order(self, ins_idx):
        ins = self.instruments[ins_idx]
        om = self.ins2om[ins.InstrumentID]
        bids, asks = om.get_untraded_orders()
        for order in bids:
            self.send_cancel_order(order)
        for order in asks:
            self.send_cancel_order(order)

    def get_price_list(self):
        for op in self.instruments[:36]:
            self.options_prices.append(op.StrikePrice)

        for op in self.instruments[:-1]:
            self.options_names.append(op.InstrumentID)

    def close_all(self):
        for pos in range(len(self.instruments)-1):
            ins = self.instruments[pos]
            om = self.ins2om[ins.InstrumentID]
            long_pos_number = om.get_long_position_closeable()
            if long_pos_number>0:
                order = om.place_market_order(self.next_order_ref(), PHX_FTDC_D_Sell, PHX_FTDC_OF_Close, long_pos_number)
                self.send_input_order(order)

            short_pos_number = om.get_short_position_closeable()
            if short_pos_number > 0:
                order=om.place_market_order(self.next_order_ref(), PHX_FTDC_D_Buy, PHX_FTDC_OF_Close, short_pos_number)
                self.send_input_order(order)

            bids, asks = om.get_untraded_orders()
            for order in bids:
                self.send_cancel_order(order)
            for order in asks:
                self.send_cancel_order(order)

    def market_maker_strategy(self):
        self.close_all()
        index = self.ins2index["UBIQ"]
        ubi_price=self.md_list[index][-1].LastPrice

        down_price=ubi_price*0.90
        up_price=ubi_price*1.10
        l_pos=r_pos=-1
        for pos in range(len(self.options_prices)):
            if self.options_prices[pos]>=down_price and l_pos==-1:
                l_pos=pos
            if self.options_prices[pos]<=up_price:
                r_pos=pos

        for yi_wu_option_pos in list(range(l_pos,r_pos+1))+list(range(l_pos+36,r_pos+1+36)):

            ins = self.instruments[yi_wu_option_pos]
            om = self.ins2om[ins.InstrumentID]
            # print(ins.InstrumentID)

            try:
                current_op_price=self.md_list[yi_wu_option_pos][-1].LastPrice
                current_op_ask1=self.md_list[yi_wu_option_pos][-1].AskPrice1
                current_op_bid1 = self.md_list[yi_wu_option_pos][-1].BidPrice1

                biggest_spread = 0
                if current_op_price < 0.1:
                    biggest_spread = 0.005
                elif current_op_price >= 0.1 and current_op_price < 0.2:
                    biggest_spread = 0.01
                elif current_op_price >= 0.2 and current_op_price < 0.5:
                    biggest_spread = 0.025
                elif current_op_price >= 0.5 and current_op_price <= 1.0:
                    biggest_spread = 0.05
                elif current_op_price > 1.0:
                    biggest_spread = 0.08

                if (current_op_ask1 - current_op_bid1)>biggest_spread:
                    bid_price=((current_op_ask1+current_op_bid1-biggest_spread)* 1000//2)/1000.0
                    ask_price = ((current_op_ask1 + current_op_bid1 + biggest_spread) * 1000 // 2) / 1000.0
                else:
                    bid_price = current_op_ask1-0.001
                    ask_price = current_op_bid1+0.001
            except:
                current_op_price =0.49
                bid_price = current_op_price - (0.025 * 1000 // 3) / 1000.0
                ask_price = current_op_price + (0.025 * 1000 // 3) / 1000.0

            if ask_price<=0.001 or bid_price<=0.001:
                continue

            bid_order = om.place_limit_order(self.next_order_ref(), PHX_FTDC_D_Buy , PHX_FTDC_OF_Open,bid_price, 10)
            self.send_input_order(bid_order)
            self.market_bid_offer.append(bid_order)

            ask_order = om.place_limit_order(self.next_order_ref(), PHX_FTDC_D_Sell, PHX_FTDC_OF_Open, ask_price, 10)
            self.send_input_order(ask_order)

            self.market_ask_offer.append(ask_order)
            self.market_ops.append(yi_wu_option_pos)
            self.market_data_updated[yi_wu_option_pos] = False
        self.is_any_updated = False
        # exit(1)

    def run_strategy(self):
        # FOR_EACH_INSTRUMENT
        for i in range(self.inst_num):
            if randint(0, 5) == 1:
                self.random_input_order(i)
            if randint(0, 5) == 1:
                self.random_cancel_order(i)
            self.market_data_updated[i] = False  # reset flag
        self.is_any_updated = False  # reset flag


if __name__ == '__main__':
    parser = OptionParser()
    parser.add_option("-i", "--ip", dest="ip", help="server ip")
    parser.add_option("-p", "--port", dest="port", help="server ip")
    parser.add_option("-u", "--user_id", dest="user_id", help="user id")
    parser.add_option("-a", "--password", dest="password", help="password")
    (options, args) = parser.parse_args()
    server_ip = '106.120.131.90'
    order_port = 9000
    user_id = 41
    password = '8V2pmCbX'

    if options.ip:
        server_ip = options.ip
    if options.port:
        order_port = int(options.port)
    if options.user_id:
        user_id = int(options.user_id)
    if options.password:
        password = options.password

    client = MyClient()
    client.serverHost = server_ip
    client.serverOrderPort = order_port
    client.serverRtnPort = order_port + 1
    client.serverQryPort = order_port + 2
    client.serverMDPort = order_port + 3
    client.m_UserID = user_id
    client.m_Passwd = password

    if client.Init():
        print("init success")
        resetted = True
        while True:
            if client.game_status is None or (not client.m_pUserApi.all_connected):
                print("server not started")
                time.sleep(1)
            elif client.game_status.GameStatus == 0:
                print("game not started, waitting for start")
                time.sleep(1)
            elif client.game_status.GameStatus == 1:
                resetted = False
                # print(client.game_status)
                # client.run_strategy()
                client.market_maker_strategy()
                time.sleep(0.1)
                # print("hhh")
            elif client.game_status.GameStatus == 2:
                print("game settling")
                time.sleep(1)
            elif client.game_status.GameStatus == 3:
                print("game settled, waiting for next round")
                if not resetted:
                    client.reset()
                    resetted = True
                    print("client resetted")
                time.sleep(1)
            elif client.game_status.GameStatus == 4:
                print("game finished")
                break
    else:
        print("init failed")



