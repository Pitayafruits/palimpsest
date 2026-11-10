using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.Drawing.Drawing2D;
using System.ComponentModel;
using System.IO;
using System.Net;
using System.Linq;
using System.Security.Cryptography;
using System.Text;
using System.Text.RegularExpressions;
using System.Threading;
using System.Web.Script.Serialization;
using System.Windows.Forms;

public class Binding {
    public string id { get; set; }
    public string page_id { get; set; }
    public string local_file { get; set; }
    public string initial_sync { get; set; }
    public override string ToString() { return Path.GetFileName(local_file) + "  ↔  " + ServerSettings.Site + "/pages/viewpage.action?pageId=" + page_id; }
}
public class Catalogue {
    public List<Binding> bindings { get; set; }
    public string last_id { get; set; }
    public string username { get; set; }
    public Catalogue() { bindings = new List<Binding>(); username = ""; }
}

public class SyncWindow : Form {
    readonly string appRoot = AppDomain.CurrentDomain.BaseDirectory;
    readonly string root = ServerSettings.DataRoot;
    readonly JavaScriptSerializer json = new JavaScriptSerializer();
    readonly TextBox page = new TextBox(), file = new TextBox(), user = new TextBox(), password = new TextBox();
    readonly ComboBox direction = new AppleCombo();
    readonly Button browse = new AppleButton(), start = new AppleButton(), stop = new AppleButton();
    readonly Label status = new Label();
    readonly LinkLabel openDocument = new LinkLabel();
    readonly Button login = new AppleButton();
    readonly LinkLabel logout = new LinkLabel();
    readonly Label account = new Label();
    Label passwordCaption;
    readonly CredentialStore credentialStore;
    LoginCredentials session;
    bool authenticating, logoutPending, windowClosing;
    readonly System.Windows.Forms.Timer timer = new System.Windows.Forms.Timer();
    Catalogue catalogue;
    Process worker;
    string activeDir;
    string contextPageId;
    bool updatingFields;
    bool catalogueReady;

    public SyncWindow() : this(null) { }
    public SyncWindow(string credentialPath) {
        credentialStore = new CredentialStore(credentialPath);
        Text = "Palimpsest · Markdown ↔ Confluence";
        AutoScaleMode = AutoScaleMode.None;
        ClientSize = new Size(900, 412);
        MinimumSize = Size; MaximumSize = Size;
        MaximizeBox = false;
        StartPosition = FormStartPosition.CenterScreen;
        Font = new Font("Microsoft YaHei UI", 10);
        BackColor = Color.FromArgb(245,245,247);
        ForeColor = Color.FromArgb(29,29,31);
        DoubleBuffered = true;
        LabelAt("文档链接", 48, 46, 135, 40);
        AddInput(page, 190, 46, 550);
        openDocument.Text = "打开文档"; openDocument.SetBounds(760, 46, 90, 40);
        openDocument.LinkColor = Color.FromArgb(0,113,227); openDocument.ActiveLinkColor = Color.FromArgb(0,94,190);
        openDocument.VisitedLinkColor = openDocument.LinkColor; openDocument.TextAlign = ContentAlignment.MiddleCenter;
        openDocument.LinkBehavior = LinkBehavior.HoverUnderline; openDocument.BackColor = Color.White;
        openDocument.LinkClicked += OpenDocument; Controls.Add(openDocument);
        LabelAt("本地 Markdown", 48, 102, 135, 40);
        AddInput(file, 190, 102, 550);
        AddButton(browse, "选择文件…", 752, 102, 98, Browse);
        LabelAt("首次同步方向", 48, 158, 135, 40);
        direction.SetBounds(190, 158, 660, 40); StyleCombo(direction);
        direction.Items.Add("以本地为准 → 更新网页正文"); direction.Items.Add("以网页为准 → 更新本地 Markdown");
        direction.SelectedIndex = 0; Controls.Add(direction);
        LabelAt("Confluence 账号", 48, 214, 135, 40);
        AddInput(user, 190, 214, 225);
        passwordCaption = LabelAt("密码", 440, 214, 55, 40);
        password.UseSystemPasswordChar = true; AddInput(password, 500, 214, 230);
        AddButton(login, "登录", 752, 214, 98, delegate { Authenticate(null); });
        account.SetBounds(190,214,550,40); account.BackColor = Color.White;
        account.TextAlign = ContentAlignment.MiddleLeft; account.UseMnemonic = false; Controls.Add(account);
        logout.Text = "退出登录"; logout.SetBounds(760,214,90,40); logout.BackColor = Color.White;
        logout.LinkColor = Color.FromArgb(0,113,227); logout.ActiveLinkColor = logout.LinkColor;
        logout.VisitedLinkColor = logout.LinkColor; logout.TextAlign = ContentAlignment.MiddleCenter;
        logout.LinkBehavior = LinkBehavior.HoverUnderline; logout.LinkClicked += Logout; Controls.Add(logout);
        AddButton(start, "开始同步", 24, 300, 190, Begin); ((AppleButton)start).Primary = true;
        AddButton(stop, "停止同步", 226, 300, 130, delegate { RequestStop(); status.Text = "正在停止…"; });
        status.SetBounds(28, 356, 844, 44); status.ForeColor = Color.FromArgb(110,110,115); Controls.Add(status);
        page.TextChanged += PageChanged;
        file.TextChanged += delegate { UpdateBaseline(); };
        page.Leave += delegate {
            AutoSave();
            try { page.Text = PageUrl(page.Text); page.SelectionStart = 0; } catch(ArgumentException) { }
        };
        file.Leave += delegate { AutoSave(); };
        direction.SelectedIndexChanged += delegate { AutoSave(); };
        user.Leave += delegate { AutoSave(); };
        timer.Interval = 1000; timer.Tick += RefreshStatus; timer.Start();
        FormClosing += delegate { windowClosing = true; RequestStop(); timer.Stop(); };
        Directory.CreateDirectory(root);
        LoadCatalogue();
        RestoreLogin();
        stop.Enabled = false;
    }

    protected override void OnPaint(PaintEventArgs e) {
        base.OnPaint(e);
        e.Graphics.SmoothingMode = SmoothingMode.AntiAlias;
        foreach(var rect in new Rectangle[]{new Rectangle(24,24,852,252)}) {
            using(var shadow = AppleDrawing.Round(new Rectangle(rect.X,rect.Y+2,rect.Width,rect.Height),14))
            using(var brush = new SolidBrush(Color.FromArgb(11,0,0,0))) e.Graphics.FillPath(brush, shadow);
            using(var path = AppleDrawing.Round(rect,14)) {
                e.Graphics.FillPath(Brushes.White,path);
                using(var pen = new Pen(Color.FromArgb(229,229,234))) e.Graphics.DrawPath(pen,path);
            }
        }
    }
    void StyleCombo(ComboBox combo) {
        combo.DropDownStyle = ComboBoxStyle.DropDownList;
        combo.FlatStyle = FlatStyle.Flat;
        combo.BackColor = Color.White;
        combo.ForeColor = ForeColor;
        combo.DrawMode = DrawMode.OwnerDrawFixed;
        combo.ItemHeight = 32;
        combo.DrawItem += delegate(object sender, DrawItemEventArgs e) {
            if(e.Index < 0) return;
            bool selected = (e.State & DrawItemState.Selected) != 0 && (e.State & DrawItemState.ComboBoxEdit) == 0;
            using(var brush = new SolidBrush(selected ? Color.FromArgb(0,113,227) : Color.White)) e.Graphics.FillRectangle(brush,e.Bounds);
            TextRenderer.DrawText(e.Graphics,combo.GetItemText(combo.Items[e.Index]),combo.Font,
                new Rectangle(e.Bounds.X+8,e.Bounds.Y,e.Bounds.Width-16,e.Bounds.Height),
                !combo.Enabled ? Color.FromArgb(160,160,165) : selected ? Color.White : ForeColor,
                TextFormatFlags.VerticalCenter | TextFormatFlags.EndEllipsis | TextFormatFlags.NoPrefix | TextFormatFlags.SingleLine);
        };
    }
    void AddInput(TextBox input, int x, int y, int width) {
        var shell = new AppleInput(input);
        shell.SetBounds(x,y,width,40); Controls.Add(shell);
    }
    Label LabelAt(string text, int x, int y, int width, int height) {
        var label = new Label(); label.Text = text; label.SetBounds(x,y,width,height);
        label.TextAlign = ContentAlignment.MiddleLeft; label.BackColor = Color.White;
        Controls.Add(label); return label;
    }
    void AddButton(Button button, string text, int x, int y, int width, EventHandler click) {
        button.Text = text; button.BackColor = y >= 300 ? BackColor : Color.White;
        button.SetBounds(x,y,width,38); button.Click += click; Controls.Add(button);
    }
    string CataloguePath { get { return Path.Combine(root, "associations.json"); } }
    string BindingDir(string id) { return Path.Combine(root, "bindings", id); }
    static string PageUrl(string value) {
        return "" + ServerSettings.Site + "/pages/viewpage.action?pageId=" + ParsePage(value);
    }
    void OpenDocument(object sender, LinkLabelLinkClickedEventArgs e) {
        try {
            Process.Start(new ProcessStartInfo { FileName=PageUrl(page.Text), UseShellExecute=true });
        } catch(Exception error) { ShowError(error); }
    }
    static string ParsePage(string value) {
        value = value.Trim();
        if (Regex.IsMatch(value, @"^[1-9][0-9]*$")) return value;
        Uri url;
        if (!Uri.TryCreate(value, UriKind.Absolute, out url) || url.Scheme != "https" || url.GetLeftPart(UriPartial.Authority) != new Uri(ServerSettings.Site).GetLeftPart(UriPartial.Authority) || url.AbsolutePath != new Uri(ServerSettings.Site).AbsolutePath.TrimEnd('/') + "/pages/viewpage.action" || url.UserInfo.Length != 0)
            throw new ArgumentException("请输入数字 pageId，或此 Confluence 站点的页面链接。");
        var match = Regex.Match(url.Query, @"(?:\?|&)pageId=([1-9][0-9]*)(?:&|$)");
        if (!match.Success) throw new ArgumentException("链接中未找到 pageId，请复制页面数字 ID。");
        return match.Groups[1].Value;
    }
    static string Identity(string pageId, string localFile) {
        using (var sha = SHA256.Create()) {
            var bytes = sha.ComputeHash(Encoding.UTF8.GetBytes(pageId + "\n" + Path.GetFullPath(localFile).ToLowerInvariant()));
            return BitConverter.ToString(bytes).Replace("-", "").ToLowerInvariant();
        }
    }
    void AtomicJson(string path, object data) {
        string temporary = path + ".tmp";
        File.WriteAllText(temporary, json.Serialize(data), new UTF8Encoding(false));
        if (File.Exists(path)) File.Replace(temporary, path, null); else File.Move(temporary, path);
    }
    void LoadCatalogue() {
        updatingFields = true;
        try {
            catalogue = File.Exists(CataloguePath) ? json.Deserialize<Catalogue>(File.ReadAllText(CataloguePath, Encoding.UTF8)) : new Catalogue();
            if (catalogue == null || catalogue.bindings == null) throw new Exception("文档记录格式不正确。");
            user.Text = catalogue.username ?? "";
            var last = catalogue.bindings.Find(b => b.id == catalogue.last_id);
            if(last != null) {
                contextPageId = last.page_id;
                page.Text = PageUrl(last.page_id); page.SelectionStart = 0;
                file.Text = last.local_file;
                direction.SelectedIndex = last.initial_sync == "remote" ? 1 : 0;
            }
            catalogueReady = true;
        } catch(Exception error) {
            catalogueReady = false; start.Enabled = false;
            status.Text = "读取文档记录失败，请保留 associations.json 并检查：" + error.Message;
        } finally { updatingFields = false; UpdateBaseline(); }
    }
    void PageChanged(object sender, EventArgs e) {
        if(updatingFields || !catalogueReady) return;
        string pageId;
        try { pageId = ParsePage(page.Text); }
        catch { UpdateBaseline(); return; }
        if(pageId != contextPageId) {
            updatingFields = true;
            try {
                contextPageId = pageId;
                var known = catalogue.bindings.Find(b => b.page_id == pageId);
                file.Text = known == null ? "" : known.local_file;
                direction.SelectedIndex = known != null && known.initial_sync == "remote" ? 1 : 0;
                status.Text = "";
            } finally { updatingFields = false; }
        }
        UpdateBaseline();
    }
    void AutoSave() {
        if(updatingFields || !catalogueReady || (worker != null && !worker.HasExited)) return;
        try {
            ParsePage(page.Text);
            if(!File.Exists(file.Text.Trim()) || !String.Equals(Path.GetExtension(file.Text.Trim()), ".md", StringComparison.OrdinalIgnoreCase)) return;
            SaveBinding();
        } catch(Exception error) { ShowError(error); }
    }
    void Browse(object sender, EventArgs e) {
        using(var picker = new OpenFileDialog()) {
            picker.Title = "选择要关联的 Markdown 文件";
            picker.Filter = "Markdown 文档 (*.md)|*.md"; picker.CheckFileExists = true; picker.Multiselect = false;
            if(File.Exists(file.Text)) picker.FileName = file.Text;
            if(picker.ShowDialog(this) == DialogResult.OK) { file.Text = picker.FileName; AutoSave(); }
        }
    }
    Binding SaveBinding() {
        if(!catalogueReady) throw new InvalidOperationException("无法读取文档记录，请先检查配置文件。");
        string pageId = ParsePage(page.Text);
        if(String.IsNullOrWhiteSpace(file.Text)) throw new ArgumentException("请选择 Markdown 文件。");
        string localFile = Path.GetFullPath(file.Text.Trim());
        if(!File.Exists(localFile) || !String.Equals(Path.GetExtension(localFile), ".md", StringComparison.OrdinalIgnoreCase))
            throw new ArgumentException("请选择已存在的 Markdown（.md）文件。");
        string id = Identity(pageId, localFile);
        var current = catalogue.bindings.Find(b => b.id == id);
        var binding = new Binding {id=id, page_id=pageId, local_file=localFile,
            initial_sync=direction.SelectedIndex == 1 ? "remote" : "local"};
        string dir = BindingDir(id);
        Directory.CreateDirectory(dir);
        // A relationship that was replaced must not silently reuse its old baseline.
        string state = Path.Combine(dir,"state.json");
        if(current == null && File.Exists(state)) {
            string history = Path.Combine(dir,"state-history"); Directory.CreateDirectory(history);
            File.Move(state,Path.Combine(history,DateTime.UtcNow.Ticks + ".json"));
        }
        AtomicJson(Path.Combine(dir, "mapping.json"), new {
            base_url=ServerSettings.Site, page_id=pageId, local_file=localFile,
            initial_sync=binding.initial_sync, poll_seconds=30, settle_seconds=5,
            initial_remote_backup=true, conflict_policy="preserve_both"
        });
        var next = catalogue.bindings.Where(b => b.page_id != pageId && !String.Equals(b.local_file,localFile,StringComparison.OrdinalIgnoreCase)).ToList();
        next.Add(binding);
        var updated = new Catalogue {bindings=next, last_id=id, username=user.Text.Trim()};
        AtomicJson(CataloguePath,updated);
        catalogue = updated; contextPageId = pageId;
        UpdateBaseline();
        return binding;
    }
    void UpdateBaseline() {
        try {
            string id = Identity(ParsePage(page.Text), file.Text.Trim());
            bool exists = catalogueReady && catalogue.bindings.Any(b => b.id == id) && File.Exists(Path.Combine(BindingDir(id), "state.json"));
            direction.Enabled = !exists && (worker == null || worker.HasExited);
        } catch { direction.Enabled = worker == null || worker.HasExited; }
    }
    void SetRunning(bool running) {
        foreach(Control control in new Control[]{browse,page,file,user,password,start}) control.Enabled = !running;
        stop.Enabled = running;
        if(!running) UpdateBaseline(); else direction.Enabled = false;
        ApplyLoginUI();
    }
    void RequestStop() {
        if(worker != null && !worker.HasExited && activeDir != null)
            File.WriteAllText(Path.Combine(activeDir, "stop.request"), "stop");
    }
    void RefreshStatus(object sender, EventArgs e) {
        if(worker == null) return;
        try {
            string statusPath = Path.Combine(activeDir, "status.json");
            if(File.Exists(statusPath)) {
                var data = json.Deserialize<Dictionary<string, object>>(File.ReadAllText(statusPath, Encoding.UTF8));
                status.Text = "页面 " + ParsePage(page.Text) + "  ·  " + data["time"] + "\n" + data["message"];
                if(data.ContainsKey("error_code") && Convert.ToString(data["error_code"]) == "auth_expired") {
                    try { credentialStore.Delete(); } catch(Exception) { status.Text = "登录已失效，清理本地登录记录失败，请手动退出登录。"; }
                    logoutPending = true;
                }
            }
            if(worker.HasExited) {
                SetRunning(false);
                if(worker.ExitCode != 0) status.Text = "同步进程异常退出，请查看对应关联目录内的 worker-error.log。";
                worker.Dispose(); worker = null;
                if(logoutPending) { CompleteLogout(); status.Text = "已退出登录，请重新登录。"; }
            }
        } catch(IOException) { } catch(ArgumentException) { }
    }
    void ShowError(Exception error) { status.Text = error.Message; }
    void ApplyLoginUI() {
        bool signedIn = session != null;
        user.Parent.Visible = password.Parent.Visible = passwordCaption.Visible = login.Visible = !signedIn;
        account.Visible = logout.Visible = signedIn;
        account.Text = signedIn ? session.username : "";
        user.Enabled = password.Enabled = login.Enabled = !authenticating && (worker == null || worker.HasExited);
        logout.Enabled = !logoutPending;
        start.Enabled = catalogueReady && !authenticating && (worker == null || worker.HasExited);
    }
    void RestoreLogin() {
        try {
            session = credentialStore.Load();
            if(session != null) user.Text = session.username;
        } catch(Exception) {
            session = null;
            status.Text = "无法恢复登录，请重新登录。";
        }
        ApplyLoginUI();
    }
    void Authenticate(Action afterLogin) {
        if(authenticating || session != null) return;
        string username = user.Text.Trim(), secret = password.Text;
        if(username.Length == 0 || secret.Length == 0) { status.Text = "请输入账号和密码。"; return; }
        authenticating = true; ApplyLoginUI(); status.Text = "正在登录…";
        var task = new BackgroundWorker();
        task.DoWork += delegate(object sender, DoWorkEventArgs args) {
            args.Result = LoginClient.Verify(username,secret);
        };
        task.RunWorkerCompleted += delegate(object sender, RunWorkerCompletedEventArgs args) {
            authenticating = false;
            if(windowClosing || IsDisposed) { task.Dispose(); return; }
            try {
                if(args.Error != null) throw args.Error;
                var verified = (LoginCredentials)args.Result;
                credentialStore.Save(verified);
                session = verified; user.Text = session.username; password.Clear();
                if(catalogueReady) { catalogue.username = session.username; AtomicJson(CataloguePath,catalogue); }
                status.Text = "已登录。";
                ApplyLoginUI();
                if(afterLogin != null) afterLogin();
            } catch(Exception error) {
                ApplyLoginUI(); ShowError(error);
            } finally { task.Dispose(); }
        };
        task.RunWorkerAsync();
    }
    void Logout(object sender, LinkLabelLinkClickedEventArgs e) {
        try {
            credentialStore.Delete();
            if(worker != null && !worker.HasExited) {
                logoutPending = true; RequestStop(); ApplyLoginUI();
                status.Text = "正在停止同步并退出登录…";
            } else { CompleteLogout(); status.Text = "已退出登录。"; }
        } catch(Exception error) { ShowError(error); }
    }
    void CompleteLogout() {
        if(session != null) { session.password = ""; session.username = ""; }
        session = null; logoutPending = false; password.Clear(); user.Clear();
        if(catalogueReady) { catalogue.username = ""; AtomicJson(CataloguePath,catalogue); }
        ApplyLoginUI(); user.Focus();
    }
    void Begin(object sender, EventArgs e) {
        if(authenticating || (worker != null && !worker.HasExited)) return;
        if(session == null) { Authenticate(delegate { Begin(sender,e); }); return; }
        try {
            var binding = SaveBinding();
            activeDir = BindingDir(binding.id);
            if(!File.Exists(Path.Combine(activeDir,"state.json"))) {
                string effect = binding.initial_sync == "local" ? "本地 Markdown 将替换此网页的正文。" : "此网页的正文将替换本地 Markdown。";
                if(MessageBox.Show(this, "页面：" + binding.page_id + "\n本地：" + binding.local_file + "\n\n" + effect + "\n覆盖前会保存原内容，页面标题不变。", "确认首次同步方向", MessageBoxButtons.OKCancel, MessageBoxIcon.Information) != DialogResult.OK) return;
            }
            string stopFile = Path.Combine(activeDir,"stop.request"); if(File.Exists(stopFile)) File.Delete(stopFile);
            string statusFile = Path.Combine(activeDir,"status.json"); if(File.Exists(statusFile)) File.Delete(statusFile);
            var info = new ProcessStartInfo {
                FileName=Path.Combine(appRoot,"engine","SyncCore.exe"),
                Arguments="--headless --config \"" + Path.Combine(activeDir,"mapping.json") + "\"",
                WorkingDirectory=root, UseShellExecute=false, CreateNoWindow=true, RedirectStandardInput=true, RedirectStandardError=true
            };
            info.EnvironmentVariables["PALIMPSEST_DATA"] = root;
            worker = new Process(); worker.StartInfo = info;
            string errorFile = Path.Combine(activeDir,"worker-error.log");
            worker.ErrorDataReceived += delegate(object obj, DataReceivedEventArgs args) { if(args.Data != null) File.AppendAllText(errorFile,args.Data + Environment.NewLine); };
            worker.Start(); worker.BeginErrorReadLine();
            var credentials = new Dictionary<string,string>{{"username",session.username},{"password",session.password}};
            using(var writer = new StreamWriter(worker.StandardInput.BaseStream,new UTF8Encoding(false))) writer.WriteLine(json.Serialize(credentials));
            credentials.Clear(); password.Clear(); SetRunning(true);
            status.Text = "正在连接页面 " + binding.page_id + "…";
        } catch(Exception error) { ShowError(error); }
    }
    [STAThread]
    static void Main() {
        using(var mutex = new Mutex(false, @"Local\PalimpsestV2")) {
            bool held;
            try { held = mutex.WaitOne(0); } catch(AbandonedMutexException) { held=true; }
            if(!held) { MessageBox.Show("文档同步窗口已打开，请切换到已有窗口。"); return; }
            try { Application.EnableVisualStyles(); Application.SetCompatibleTextRenderingDefault(false); if(ServerSettings.Configure()) Application.Run(new SyncWindow()); }
            finally { mutex.ReleaseMutex(); }
        }
    }
}


static class AppleDrawing {
    public static GraphicsPath Round(Rectangle rect, int radius) {
        var path = new GraphicsPath(); int d = radius * 2;
        rect.Width -= 1; rect.Height -= 1;
        path.AddArc(rect.X,rect.Y,d,d,180,90);
        path.AddArc(rect.Right-d,rect.Y,d,d,270,90);
        path.AddArc(rect.Right-d,rect.Bottom-d,d,d,0,90);
        path.AddArc(rect.X,rect.Bottom-d,d,d,90,90);
        path.CloseFigure(); return path;
    }
}
class AppleInput : Panel {
    readonly TextBox input;
    public AppleInput(TextBox field) {
        input = field; DoubleBuffered = true; BackColor = Color.White;
        input.BorderStyle = BorderStyle.None; input.BackColor = Color.White;
        input.AutoSize = false;
        Controls.Add(input);
        input.FontChanged += delegate { PerformLayout(); };
        input.GotFocus += delegate { Invalidate(); }; input.LostFocus += delegate { Invalidate(); };
        input.EnabledChanged += delegate { input.BackColor = input.Enabled ? Color.White : Color.FromArgb(248,248,250); Invalidate(); };
    }
    protected override void OnLayout(LayoutEventArgs e) {
        base.OnLayout(e);
        int height = Math.Max(input.PreferredHeight,input.Font.Height) + 6;
        input.SetBounds(12,Math.Max(3,(Height-height)/2),Math.Max(1,Width-24),height);
    }
    protected override void OnPaint(PaintEventArgs e) {
        base.OnPaint(e); e.Graphics.SmoothingMode = SmoothingMode.AntiAlias;
        using(var path = AppleDrawing.Round(ClientRectangle,8)) {
            using(var brush = new SolidBrush(input.Enabled ? Color.White : Color.FromArgb(248,248,250))) e.Graphics.FillPath(brush,path);
            using(var pen = new Pen(input.Focused ? Color.FromArgb(0,113,227) : Color.FromArgb(218,218,223),input.Focused ? 2 : 1)) e.Graphics.DrawPath(pen,path);
        }
    }
}
class AppleButton : Button {
    public bool Primary;
    bool hover, pressed;
    public AppleButton() {
        SetStyle(ControlStyles.UserPaint | ControlStyles.AllPaintingInWmPaint | ControlStyles.OptimizedDoubleBuffer,true);
        FlatStyle = FlatStyle.Flat; FlatAppearance.BorderSize = 0;
        UseVisualStyleBackColor = false; Cursor = Cursors.Hand;
    }
    protected override void OnMouseEnter(EventArgs e) { hover=true; Invalidate(); base.OnMouseEnter(e); }
    protected override void OnMouseLeave(EventArgs e) { hover=pressed=false; Invalidate(); base.OnMouseLeave(e); }
    protected override void OnMouseDown(MouseEventArgs e) { pressed=true; Invalidate(); base.OnMouseDown(e); }
    protected override void OnMouseUp(MouseEventArgs e) { pressed=false; Invalidate(); base.OnMouseUp(e); }
    protected override void OnPaint(PaintEventArgs e) {
        e.Graphics.Clear(BackColor); e.Graphics.SmoothingMode = SmoothingMode.AntiAlias;
        Color fill = !Enabled ? Color.FromArgb(235,235,239) : Primary ? (pressed ? Color.FromArgb(0,88,180) : hover ? Color.FromArgb(0,122,245) : Color.FromArgb(0,113,227)) : (pressed ? Color.FromArgb(223,223,228) : hover ? Color.FromArgb(235,235,240) : Color.FromArgb(242,242,247));
        using(var path = AppleDrawing.Round(ClientRectangle,8)) {
            using(var brush = new SolidBrush(fill)) e.Graphics.FillPath(brush,path);
        }
        Color text = !Enabled ? Color.FromArgb(165,165,172) : Primary ? Color.White : Color.FromArgb(29,29,31);
        TextRenderer.DrawText(e.Graphics,Text,Font,ClientRectangle,text,TextFormatFlags.HorizontalCenter | TextFormatFlags.VerticalCenter | TextFormatFlags.SingleLine);
        if(Focused && ShowFocusCues) {
            var rect = ClientRectangle; rect.Inflate(-3,-3);
            using(var path = AppleDrawing.Round(rect,6)) using(var pen = new Pen(Primary ? Color.White : Color.FromArgb(0,113,227))) { pen.DashStyle=DashStyle.Dot; e.Graphics.DrawPath(pen,path); }
        }
    }
}

class AppleCombo : ComboBox {
    protected override void WndProc(ref Message message) {
        base.WndProc(ref message);
        if(message.Msg != 0xF || !IsHandleCreated) return;
        using(var g = Graphics.FromHwnd(Handle)) {
            g.SmoothingMode = SmoothingMode.AntiAlias;
            using(var pen = new Pen(Color.White,4)) g.DrawRectangle(pen,1,1,Width-3,Height-3);
            g.FillRectangle(Brushes.White,Width-29,2,27,Height-4);
            using(var path = AppleDrawing.Round(ClientRectangle,8))
            using(var pen = new Pen(Focused ? Color.FromArgb(0,113,227) : Color.FromArgb(218,218,223))) g.DrawPath(pen,path);
            using(var brush = new SolidBrush(Enabled ? Color.FromArgb(110,110,115) : Color.FromArgb(185,185,190))) {
                int x=Width-17,y=Height/2;
                g.FillPolygon(brush,new Point[]{new Point(x-4,y-2),new Point(x+4,y-2),new Point(x,y+2)});
            }
        }
    }
}
