//+------------------------------------------------------------------+
//| QTC_TickSync.mq5 - QuanTechCap: đồng bộ tick cho symbol tùy chỉnh |
//| *_Tickstory theo dữ liệu Dukascopy mà data-server đã cập nhật.     |
//|                                                                    |
//| Chỉ làm việc với symbol TÙY CHỈNH có đuôi "_Tickstory". Không đặt  |
//| lệnh, không đụng tài khoản giao dịch.                              |
//| Nhận lệnh qua Common\Files\QuanTechCap\mt5sync\req\<số>_<x>.txt    |
//| (dòng key=value), trả kết quả ở ...\out\<số>.txt / .csv.           |
//|   cmd=info                 -> tick cuối của mọi symbol *_Tickstory |
//|   cmd=export symbol= from_msc= to_msc= -> csv các tick trong khoảng|
//|   cmd=replace symbol= file= from_msc= to_msc= count=               |
//|        -> CustomTicksReplace bằng các tick trong file nhị phân     |
//|           (bản ghi 44 byte: long msc, double bid, ask, last,       |
//|            ulong volume, uint flags), kiểm lại số tick sau khi ghi |
//+------------------------------------------------------------------+
#property service
#property copyright "QuanTechCap"
#property version   "1.00"

#define QDIR "QuanTechCap\\mt5sync\\"
#define SUFFIX "_Tickstory"

struct QRec
  {
   long              msc;
   double            bid;
   double            ask;
   double            last;
   ulong             vol;
   uint              flags;
  };

string g_keys[];
string g_vals[];

//--- đọc file yêu cầu key=value
bool ReadReq(const string path)
  {
   ArrayResize(g_keys, 0);
   ArrayResize(g_vals, 0);
   int h = FileOpen(path, FILE_READ | FILE_TXT | FILE_ANSI | FILE_COMMON);
   if(h == INVALID_HANDLE)
      return false;
   while(!FileIsEnding(h))
     {
      string line = FileReadString(h);
      int p = StringFind(line, "=");
      if(p <= 0)
         continue;
      int n = ArraySize(g_keys);
      ArrayResize(g_keys, n + 1);
      ArrayResize(g_vals, n + 1);
      g_keys[n] = StringSubstr(line, 0, p);
      g_vals[n] = StringSubstr(line, p + 1);
      StringTrimLeft(g_vals[n]);
      StringTrimRight(g_vals[n]);
     }
   FileClose(h);
   return true;
  }

string Val(const string k)
  {
   for(int i = 0; i < ArraySize(g_keys); i++)
      if(g_keys[i] == k)
         return g_vals[i];
   return "";
  }

bool AllowedSymbol(const string s)
  {
   if(StringLen(s) <= StringLen(SUFFIX) || StringSubstr(s, StringLen(s) - StringLen(SUFFIX)) != SUFFIX)
      return false;
   bool custom = false;
   if(!SymbolExist(s, custom))
      return false;
   return custom;
  }

void WriteOut(const string id, const string text)
  {
   string tmp = QDIR + "out\\" + id + ".part";
   int h = FileOpen(tmp, FILE_WRITE | FILE_TXT | FILE_ANSI | FILE_COMMON);
   if(h == INVALID_HANDLE)
      return;
   FileWriteString(h, text);
   FileClose(h);
   FileMove(tmp, FILE_COMMON, QDIR + "out\\" + id + ".txt", FILE_COMMON | FILE_REWRITE);
  }

string TickLine(const MqlTick &t)
  {
   return StringFormat("%I64d,%.10g,%.10g,%.10g,%I64u,%u,%.10g", t.time_msc, t.bid, t.ask, t.last, t.volume, t.flags, t.volume_real);
  }

//--- tick cuối (thử vài lần vì lịch sử tick tải không đồng bộ)
int LastTicks(const string s, MqlTick &t[], int count)
  {
   for(int i = 0; i < 20 && !IsStopped(); i++)
     {
      int n = CopyTicks(s, t, COPY_TICKS_ALL, 0, count);
      if(n > 0)
         return n;
      Sleep(500);
     }
   return -1;
  }

void CmdInfo(const string id)
  {
   string out = "ok=1\nversion=1.00\nterminal_time=" + TimeToString(TimeCurrent(), TIME_DATE | TIME_SECONDS) + "\n";
   int total = SymbolsTotal(false);
   for(int i = 0; i < total; i++)
     {
      string s = SymbolName(i, false);
      if(!AllowedSymbol(s))
         continue;
      MqlTick t[];
      int n = LastTicks(s, t, 1);
      int digits = (int)SymbolInfoInteger(s, SYMBOL_DIGITS);
      if(n > 0)
         out += "sym=" + s + "," + IntegerToString(digits) + "," + TickLine(t[n - 1]) + "\n";
      else
         out += "sym=" + s + "," + IntegerToString(digits) + ",NO_TICKS," + IntegerToString(GetLastError()) + "\n";
     }
   WriteOut(id, out);
  }

void CmdExport(const string id)
  {
   string s = Val("symbol");
   if(!AllowedSymbol(s))
     {
      WriteOut(id, "ok=0\nerror=symbol khong hop le: " + s + "\n");
      return;
     }
   long from = StringToInteger(Val("from_msc")), to = StringToInteger(Val("to_msc"));
   MqlTick t[];
   int n = -1;
   for(int k = 0; k < 20 && n < 0 && !IsStopped(); k++)
     {
      n = CopyTicksRange(s, t, COPY_TICKS_ALL, (ulong)from, (ulong)to);
      if(n < 0)
         Sleep(500);
     }
   if(n < 0)
     {
      WriteOut(id, "ok=0\nerror=CopyTicksRange " + IntegerToString(GetLastError()) + "\n");
      return;
     }
   string csv = QDIR + "out\\" + id + ".csv";
   int h = FileOpen(csv, FILE_WRITE | FILE_TXT | FILE_ANSI | FILE_COMMON);
   for(int i = 0; i < n; i++)
      FileWriteString(h, TickLine(t[i]) + "\n");
   FileClose(h);
   WriteOut(id, "ok=1\ncount=" + IntegerToString(n) + "\n");
  }

void CmdReplace(const string id)
  {
   string s = Val("symbol");
   if(!AllowedSymbol(s))
     {
      WriteOut(id, "ok=0\nerror=symbol khong hop le: " + s + "\n");
      return;
     }
   string f = QDIR + "in\\" + Val("file");
   long from = StringToInteger(Val("from_msc")), to = StringToInteger(Val("to_msc"));
   int want = (int)StringToInteger(Val("count"));
   int h = FileOpen(f, FILE_READ | FILE_BIN | FILE_COMMON);
   if(h == INVALID_HANDLE)
     {
      WriteOut(id, "ok=0\nerror=khong mo duoc " + f + "\n");
      return;
     }
   QRec r[];
   int n = (int)FileReadArray(h, r);
   FileClose(h);
   if(n != want || n <= 0 || r[0].msc < from || r[n - 1].msc > to)
     {
      WriteOut(id, StringFormat("ok=0\nerror=file khong khop: n=%d want=%d\n", n, want));
      return;
     }
   MqlTick t[];
   ArrayResize(t, n);
   for(int i = 0; i < n; i++)
     {
      t[i].time = (datetime)(r[i].msc / 1000);
      t[i].time_msc = r[i].msc;
      t[i].bid = r[i].bid;
      t[i].ask = r[i].ask;
      t[i].last = r[i].last;
      t[i].volume = r[i].vol;
      t[i].volume_real = (double)r[i].vol;
      t[i].flags = r[i].flags;
     }
   ResetLastError();
   int done = CustomTicksReplace(s, from, to, t);
   if(done < 0)
     {
      WriteOut(id, "ok=0\nerror=CustomTicksReplace " + IntegerToString(GetLastError()) + "\n");
      return;
     }
   //--- kiểm lại: số tick trong khoảng phải đúng bằng số đã ghi
   MqlTick chk[];
   int m = -1;
   for(int k = 0; k < 40 && !IsStopped(); k++)
     {
      m = CopyTicksRange(s, chk, COPY_TICKS_ALL, (ulong)from, (ulong)to);
      if(m == n)
         break;
      Sleep(500);
     }
   string res = StringFormat("ok=%d\nreplaced=%d\ncheck_count=%d\n", (m == n ? 1 : 0), done, m);
   if(m > 0)
      res += "first=" + TickLine(chk[0]) + "\nlast=" + TickLine(chk[m - 1]) + "\n";
   WriteOut(id, res);
   if(m == n)
      FileDelete(f, FILE_COMMON);
  }

void WriteStatus(const string state)
  {
   int h = FileOpen(QDIR + "service_status.txt", FILE_WRITE | FILE_TXT | FILE_ANSI | FILE_COMMON);
   if(h == INVALID_HANDLE)
      return;
   FileWriteString(h, "version=1.00\nstate=" + state + "\nlocal_time=" + TimeToString(TimeLocal(), TIME_DATE | TIME_SECONDS) + "\n");
   FileClose(h);
  }

//--- xử lý yêu cầu theo thứ tự số ở đầu tên file
void ProcessRequests()
  {
   string name;
   long ids[];
   string names[];
   long hf = FileFindFirst(QDIR + "req\\*.txt", name, FILE_COMMON);
   if(hf == INVALID_HANDLE)
      return;
   do
     {
      int p = StringFind(name, "_");
      long num = StringToInteger(p > 0 ? StringSubstr(name, 0, p) : name);
      int n = ArraySize(ids);
      ArrayResize(ids, n + 1);
      ArrayResize(names, n + 1);
      ids[n] = num;
      names[n] = name;
     }
   while(FileFindNext(hf, name));
   FileFindClose(hf);
   int cnt = ArraySize(ids);
   for(int i = 0; i < cnt - 1; i++)          // sắp xếp chọn (ít file)
      for(int j = i + 1; j < cnt; j++)
         if(ids[j] < ids[i])
           {
            long a = ids[i];
            ids[i] = ids[j];
            ids[j] = a;
            string b = names[i];
            names[i] = names[j];
            names[j] = b;
           }
   for(int i = 0; i < cnt && !IsStopped(); i++)
     {
      string path = QDIR + "req\\" + names[i];
      string id = StringSubstr(names[i], 0, StringLen(names[i]) - 4);
      if(!ReadReq(path))
         continue;
      FileDelete(path, FILE_COMMON);
      string cmd = Val("cmd");
      WriteStatus("busy " + id);
      if(cmd == "info")
         CmdInfo(id);
      else
         if(cmd == "export")
            CmdExport(id);
         else
            if(cmd == "replace")
               CmdReplace(id);
            else
               WriteOut(id, "ok=0\nerror=lenh la: " + cmd + "\n");
     }
  }

void OnStart()
  {
   FolderCreate(QDIR + "req", FILE_COMMON);
   FolderCreate(QDIR + "in", FILE_COMMON);
   FolderCreate(QDIR + "out", FILE_COMMON);
   while(!IsStopped())
     {
      WriteStatus("idle");
      ProcessRequests();
      Sleep(3000);
     }
   WriteStatus("stopped");
  }
//+------------------------------------------------------------------+
