import discord
from discord.ext import commands
from discord.ui import View, Button, Modal, TextInput
import asyncio
import re
import aiohttp
from aiohttp import web
import time
import os
from datetime import datetime

# ===================== CONFIGURAÇÃO =====================
TOKEN         = os.getenv("TOKEN", "SEU_TOKEN_AQUI")
PREFIX        = "!"
WEB_PORT      = int(os.getenv("PORT", 5000))
CLIENT_ID     = os.getenv("CLIENT_ID", "1465283558342918235")
CLIENT_SECRET = os.getenv("CLIENT_SECRET", "VltQZe5OLaRPKgsbpp_bLzmpCuQTH_XL")
REDIRECT_URI  = os.getenv("REDIRECT_URI", "https://commandvirex.netlify.app/index.html")
OWNER_IDS     = set(int(x) for x in os.getenv("OWNER_IDS", "0").split(",") if x.strip().isdigit())

intents = discord.Intents.all()
bot = commands.Bot(command_prefix=PREFIX, intents=intents, help_command=None)

auto_msgs     = {}
sessions      = {}
logs_acoes    = []
punicoes      = {}   # {user_id: [{tipo, motivo, hora, moderador}]}
lista_negra   = {}   # {guild_id: [palavra1, palavra2]}

# ===================== HELPERS =====================

def is_admin(member, guild):
    return member.id == guild.owner_id or member.guild_permissions.administrator

def parse_intervalo(valor):
    match = re.fullmatch(r"(\d+)(s|m|h)", str(valor).lower())
    if not match: return None
    num, u = int(match.group(1)), match.group(2)
    return num * {"s": 1, "m": 60, "h": 3600}[u]

def resolver_mencao(guild, mencao):
    mencao = str(mencao).strip()
    if not mencao: return ""
    if mencao.lower() in ("@everyone", "everyone"): return "@everyone"
    if mencao.lower() in ("@here", "here"): return "@here"
    try:
        rid = int(mencao.replace("<@&","").replace(">",""))
        role = guild.get_role(rid)
        if role: return role.mention
    except: pass
    return mencao

def agora():
    return datetime.now().strftime("%d/%m/%Y %H:%M:%S")

def add_log(acao, detalhe, usuario="painel"):
    logs_acoes.insert(0, {"hora": agora(), "acao": acao, "detalhe": detalhe, "usuario": usuario})
    if len(logs_acoes) > 300: logs_acoes.pop()

def add_punicao(user_id, tipo, motivo, moderador):
    uid = str(user_id)
    if uid not in punicoes: punicoes[uid] = []
    punicoes[uid].insert(0, {"tipo": tipo, "motivo": motivo, "hora": agora(), "moderador": moderador})
    if len(punicoes[uid]) > 50: punicoes[uid].pop()

# ===================== OAUTH =====================

async def get_discord_user(access_token):
    async with aiohttp.ClientSession() as s:
        async with s.get("https://discord.com/api/v10/users/@me",
                         headers={"Authorization": f"Bearer {access_token}"}) as r:
            return await r.json() if r.status == 200 else None

async def check_auth(request):
    token = request.headers.get("X-Discord-Token","")
    if not token: raise web.HTTPUnauthorized(text="Token ausente")
    if token in sessions:
        sess = sessions[token]
        if time.time() < sess["expires"]: return sess
        del sessions[token]
    user = await get_discord_user(token)
    if not user: raise web.HTTPUnauthorized(text="Token inválido")
    uid = int(user["id"])
    auth = uid in OWNER_IDS
    if not auth:
        for g in bot.guilds:
            m = g.get_member(uid)
            if m and is_admin(m, g): auth = True; break
    if not auth: raise web.HTTPForbidden(text="Sem permissão")
    sess = {"user_id": uid, "username": user["username"],
            "avatar": user.get("avatar"), "expires": time.time() + 3600}
    sessions[token] = sess
    return sess

# ===================== MSG AUTO =====================

class MsgAutoView(View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(Button(label="📨 Mensagem Automática", style=discord.ButtonStyle.secondary, disabled=True))

async def enviar_automsg(canal, conteudo, banner_url=None, mencao=""):
    embed = discord.Embed(description=conteudo, color=0x2b2d31)
    if banner_url: embed.set_image(url=banner_url)
    embed.set_footer(text="Mensagem Automática")
    return await canal.send(content=mencao or None, embed=embed, view=MsgAutoView())

async def ativar_automsg(cid, canal, msg_texto, segundos, banner_url, mencao_str):
    if cid in auto_msgs:
        auto_msgs[cid]["task"].cancel()
        oid = auto_msgs[cid].get("msg_id")
        if oid:
            try: old = await canal.fetch_message(oid); await old.delete()
            except: pass
    msg_i = await enviar_automsg(canal, msg_texto, banner_url, mencao_str)
    auto_msgs[cid] = {"task": None, "conteudo": msg_texto, "intervalo": segundos,
                      "msg_id": msg_i.id, "canal": canal, "canal_nome": canal.name,
                      "banner": banner_url, "mencao": mencao_str}
    async def loop():
        while True:
            await asyncio.sleep(segundos)
            try:
                d = auto_msgs.get(cid)
                if not d: break
                oid = d.get("msg_id")
                if oid:
                    try: old = await canal.fetch_message(oid); await old.delete()
                    except: pass
                n = await enviar_automsg(canal, d["conteudo"], d.get("banner"), d.get("mencao",""))
                auto_msgs[cid]["msg_id"] = n.id
            except Exception as e: print(f"automsg err: {e}")
    auto_msgs[cid]["task"] = asyncio.ensure_future(loop())

# ===================== LISTA NEGRA — EVENTO =====================

@bot.event
async def on_message(message):
    if message.author.bot or not message.guild:
        return await bot.process_commands(message)
    gid = str(message.guild.id)
    palavras = lista_negra.get(gid, [])
    conteudo = message.content.lower()
    for p in palavras:
        if p.lower() in conteudo:
            try:
                await message.delete()
                await message.channel.send(
                    f"⚠️ {message.author.mention} sua mensagem foi removida por conter palavra proibida.",
                    delete_after=5
                )
            except: pass
            return
    await bot.process_commands(message)

# ===================== MODAIS DISCORD =====================

class AutoMsgModal(Modal, title="📨 Mensagem Automática"):
    canal_id  = TextInput(label="ID do Canal", placeholder="Ex: 123456789")
    intervalo = TextInput(label="Intervalo (ex: 30s, 5m, 2h)", placeholder="10m")
    mencao    = TextInput(label="Menção (opcional)", placeholder="@everyone | @here | ID do cargo", required=False)
    mensagem  = TextInput(label="Mensagem", style=discord.TextStyle.paragraph)
    banner    = TextInput(label="URL do Banner (opcional)", required=False, placeholder="https://...")
    async def on_submit(self, interaction: discord.Interaction):
        cid = int(self.canal_id.value.strip())
        canal = interaction.guild.get_channel(cid)
        if not canal: return await interaction.response.send_message("❌ Canal não encontrado.", ephemeral=True)
        s = parse_intervalo(self.intervalo.value.strip())
        if not s: return await interaction.response.send_message("❌ Intervalo inválido.", ephemeral=True)
        await ativar_automsg(cid, canal, self.mensagem.value, s, self.banner.value.strip() or None,
                             resolver_mencao(interaction.guild, self.mencao.value))
        await interaction.response.send_message(f"✅ Ativa em <#{cid}> a cada **{self.intervalo.value}**.", ephemeral=True)

class EditarAutoMsgModal(Modal, title="✏️ Editar Mensagem Automática"):
    canal_id = TextInput(label="ID do Canal")
    nova_msg = TextInput(label="Nova Mensagem", style=discord.TextStyle.paragraph)
    mencao   = TextInput(label="Menção (vazio = manter)", required=False)
    banner   = TextInput(label="Banner URL (vazio = manter)", required=False)
    async def on_submit(self, interaction: discord.Interaction):
        cid = int(self.canal_id.value.strip())
        if cid not in auto_msgs: return await interaction.response.send_message("❌ Não encontrado.", ephemeral=True)
        auto_msgs[cid]["conteudo"] = self.nova_msg.value
        if self.banner.value.strip(): auto_msgs[cid]["banner"] = self.banner.value.strip()
        if self.mencao.value.strip(): auto_msgs[cid]["mencao"] = resolver_mencao(interaction.guild, self.mencao.value)
        canal = auto_msgs[cid]["canal"]
        oid = auto_msgs[cid].get("msg_id")
        if oid:
            try: old = await canal.fetch_message(oid); await old.delete()
            except: pass
        n = await enviar_automsg(canal, auto_msgs[cid]["conteudo"], auto_msgs[cid].get("banner"), auto_msgs[cid].get("mencao",""))
        auto_msgs[cid]["msg_id"] = n.id
        await interaction.response.send_message("✅ Atualizada.", ephemeral=True)

class EditarIntervaloModal(Modal, title="⏱️ Editar Intervalo"):
    canal_id  = TextInput(label="ID do Canal")
    intervalo = TextInput(label="Novo Intervalo (30s, 5m, 2h)")
    async def on_submit(self, interaction: discord.Interaction):
        cid = int(self.canal_id.value.strip())
        if cid not in auto_msgs: return await interaction.response.send_message("❌ Não encontrado.", ephemeral=True)
        s = parse_intervalo(self.intervalo.value.strip())
        if not s: return await interaction.response.send_message("❌ Inválido.", ephemeral=True)
        d = auto_msgs[cid]; d["task"].cancel()
        await ativar_automsg(cid, d["canal"], d["conteudo"], s, d.get("banner"), d.get("mencao",""))
        await interaction.response.send_message(f"✅ Intervalo: **{self.intervalo.value}**.", ephemeral=True)

class PararAutoMsgModal(Modal, title="🛑 Parar Mensagem Automática"):
    canal_id = TextInput(label="ID do Canal")
    async def on_submit(self, interaction: discord.Interaction):
        cid = int(self.canal_id.value.strip())
        if cid not in auto_msgs: return await interaction.response.send_message("❌ Não encontrado.", ephemeral=True)
        auto_msgs[cid]["task"].cancel(); del auto_msgs[cid]
        await interaction.response.send_message("🛑 Parada.", ephemeral=True)

class EditarMsgModal(Modal, title="📝 Editar Mensagem do Bot"):
    canal_id = TextInput(label="ID do Canal")
    msg_id   = TextInput(label="ID da Mensagem")
    conteudo = TextInput(label="Novo Conteúdo", style=discord.TextStyle.paragraph)
    async def on_submit(self, interaction: discord.Interaction):
        try:
            canal = interaction.guild.get_channel(int(self.canal_id.value.strip()))
            msg = await canal.fetch_message(int(self.msg_id.value.strip()))
            if msg.author != bot.user: return await interaction.response.send_message("❌ Só edito minhas mensagens.", ephemeral=True)
            await msg.edit(content=self.conteudo.value)
            await interaction.response.send_message("✅ Editada.", ephemeral=True)
        except Exception as e: await interaction.response.send_message(f"❌ Erro: `{e}`", ephemeral=True)

class PingAllModal(Modal, title="📢 Ping Silencioso"):
    confirmacao = TextInput(label='Digite "confirmar"', placeholder="confirmar")
    async def on_submit(self, interaction: discord.Interaction):
        if self.confirmacao.value.strip().lower() != "confirmar":
            return await interaction.response.send_message("❌ Cancelado.", ephemeral=True)
        await interaction.response.send_message("⏳ Enviando...", ephemeral=True)
        canais = [c for c in interaction.guild.text_channels if c.permissions_for(interaction.guild.me).send_messages]
        for c in canais:
            try: msg = await c.send("."); await asyncio.sleep(1); await msg.delete()
            except: pass
        await interaction.edit_original_response(content=f"✅ Ping em **{len(canais)}** canais.")

class PainelView(View):
    def __init__(self):
        super().__init__(timeout=None)
    @discord.ui.button(label="📨 Msg Automática",   style=discord.ButtonStyle.primary,   row=0)
    async def b1(self, i, b):
        if not is_admin(i.user, i.guild): return await i.response.send_message("❌", ephemeral=True)
        await i.response.send_modal(AutoMsgModal())
    @discord.ui.button(label="✏️ Editar Msg Auto",  style=discord.ButtonStyle.secondary, row=0)
    async def b2(self, i, b):
        if not is_admin(i.user, i.guild): return await i.response.send_message("❌", ephemeral=True)
        await i.response.send_modal(EditarAutoMsgModal())
    @discord.ui.button(label="⏱️ Editar Intervalo", style=discord.ButtonStyle.secondary, row=0)
    async def b3(self, i, b):
        if not is_admin(i.user, i.guild): return await i.response.send_message("❌", ephemeral=True)
        await i.response.send_modal(EditarIntervaloModal())
    @discord.ui.button(label="🛑 Parar Msg Auto",   style=discord.ButtonStyle.danger,    row=0)
    async def b4(self, i, b):
        if not is_admin(i.user, i.guild): return await i.response.send_message("❌", ephemeral=True)
        await i.response.send_modal(PararAutoMsgModal())
    @discord.ui.button(label="📝 Editar Mensagem",  style=discord.ButtonStyle.secondary, row=1)
    async def b5(self, i, b):
        if not is_admin(i.user, i.guild): return await i.response.send_message("❌", ephemeral=True)
        await i.response.send_modal(EditarMsgModal())
    @discord.ui.button(label="📢 Ping Silencioso",  style=discord.ButtonStyle.danger,    row=1)
    async def b6(self, i, b):
        if not is_admin(i.user, i.guild): return await i.response.send_message("❌", ephemeral=True)
        await i.response.send_modal(PingAllModal())

@bot.command(name="geralserver")
async def geralserver(ctx):
    if not is_admin(ctx.author, ctx.guild): return await ctx.send("❌", delete_after=5)
    await ctx.message.delete()
    embed = discord.Embed(title="⚙️ Painel — VIREX STORE", description="Clique nos botões:", color=0x5865F2)
    embed.set_footer(text="Apenas dono e administradores podem usar.")
    await ctx.send(embed=embed, view=PainelView())

# ===================== CORS =====================

@web.middleware
async def cors_mw(request, handler):
    if request.method == "OPTIONS":
        return web.Response(status=200, headers={
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Headers": "X-Discord-Token, Content-Type",
            "Access-Control-Allow-Methods": "GET, POST, OPTIONS"})
    try: response = await handler(request)
    except web.HTTPException as e: response = web.Response(status=e.status, text=e.text)
    response.headers.update({"Access-Control-Allow-Origin": "*",
                              "Access-Control-Allow-Headers": "X-Discord-Token, Content-Type"})
    return response

# ===================== ROTAS API =====================

async def route_oauth(request):
    data = await request.json()
    code = data.get("code")
    if not code: return web.json_response({"error": "code ausente"}, status=400)
    async with aiohttp.ClientSession() as s:
        async with s.post("https://discord.com/api/v10/oauth2/token", data={
            "client_id": CLIENT_ID, "client_secret": CLIENT_SECRET,
            "grant_type": "authorization_code", "code": code, "redirect_uri": REDIRECT_URI
        }, headers={"Content-Type": "application/x-www-form-urlencoded"}) as r:
            td = await r.json()
    if "access_token" not in td: return web.json_response({"error": "OAuth falhou", "detail": td}, status=401)
    at = td["access_token"]
    user = await get_discord_user(at)
    if not user: return web.json_response({"error": "Usuário não encontrado"}, status=401)
    uid = int(user["id"])
    auth = uid in OWNER_IDS
    if not auth:
        for g in bot.guilds:
            m = g.get_member(uid)
            if m and is_admin(m, g): auth = True; break
    if not auth: return web.json_response({"error": "Sem permissão."}, status=403)
    sessions[at] = {"user_id": uid, "username": user["username"], "avatar": user.get("avatar"), "expires": time.time()+3600}
    return web.json_response({"access_token": at, "username": user["username"], "avatar": user.get("avatar"), "user_id": str(uid)})

async def route_me(request):
    sess = await check_auth(request)
    return web.json_response(sess)

async def route_status(request):
    await check_auth(request)
    return web.json_response({
        "bot": str(bot.user), "bot_id": str(bot.user.id),
        "servidores": [{"id": str(g.id), "nome": g.name, "membros": g.member_count,
                        "icon": str(g.icon.url) if g.icon else None,
                        "banner": str(g.banner.url) if g.banner else None} for g in bot.guilds],
        "msgs_auto": len(auto_msgs),
        "total_membros": sum(g.member_count for g in bot.guilds)
    })

async def route_canais(request):
    await check_auth(request)
    gid = int(request.rel_url.query.get("guild_id", 0))
    g = bot.get_guild(gid)
    if not g: return web.json_response({"error": "Servidor não encontrado"}, status=404)
    return web.json_response({"canais": [{"id": str(c.id), "nome": c.name}
        for c in g.text_channels if c.permissions_for(g.me).send_messages]})

async def route_membros(request):
    await check_auth(request)
    gid = int(request.rel_url.query.get("guild_id", 0))
    q   = request.rel_url.query.get("q", "").lower()
    g = bot.get_guild(gid)
    if not g: return web.json_response({"error": "Servidor não encontrado"}, status=404)
    membros = [m for m in g.members if not m.bot]
    if q: membros = [m for m in membros if q in m.name.lower() or (m.nick and q in m.nick.lower()) or q in str(m.id)]
    return web.json_response({"membros": [
        {"id": str(m.id), "nome": str(m), "nick": m.nick or "", "avatar": str(m.display_avatar.url)}
        for m in membros[:50]]})

async def route_membros_recentes(request):
    await check_auth(request)
    gid = int(request.rel_url.query.get("guild_id", 0))
    g = bot.get_guild(gid)
    if not g: return web.json_response({"error": "Servidor não encontrado"}, status=404)
    membros = sorted([m for m in g.members if not m.bot and m.joined_at],
                     key=lambda m: m.joined_at, reverse=True)[:10]
    return web.json_response({"membros": [
        {"id": str(m.id), "nome": str(m), "avatar": str(m.display_avatar.url),
         "entrou": m.joined_at.strftime("%d/%m/%Y %H:%M")}
        for m in membros]})

async def route_online(request):
    await check_auth(request)
    gid = int(request.rel_url.query.get("guild_id", 0))
    g = bot.get_guild(gid)
    if not g: return web.json_response({"error": "Servidor não encontrado"}, status=404)
    online = sum(1 for m in g.members if not m.bot and m.status != discord.Status.offline)
    return web.json_response({"online": online, "total": g.member_count})

async def route_cargos(request):
    await check_auth(request)
    gid = int(request.rel_url.query.get("guild_id", 0))
    g = bot.get_guild(gid)
    if not g: return web.json_response({"error": "Servidor não encontrado"}, status=404)
    return web.json_response({"cargos": [
        {"id": str(r.id), "nome": r.name, "cor": str(r.color)}
        for r in g.roles if r.name != "@everyone"]})

async def route_automsg_list(request):
    await check_auth(request)
    return web.json_response({"msgs": [
        {"canal_id": str(cid), "canal_nome": d.get("canal_nome", "?"),
         "conteudo": d["conteudo"], "intervalo": d["intervalo"],
         "mencao": d.get("mencao",""), "banner": d.get("banner","")}
        for cid, d in auto_msgs.items()]})

async def route_automsg_criar(request):
    sess = await check_auth(request)
    data = await request.json()
    gid = int(data.get("guild_id",0)); cid = int(data.get("canal_id",0))
    s = parse_intervalo(data.get("intervalo","10m"))
    if not s: return web.json_response({"error": "Intervalo inválido"}, status=400)
    g = bot.get_guild(gid)
    if not g: return web.json_response({"error": "Servidor não encontrado"}, status=404)
    c = g.get_channel(cid)
    if not c: return web.json_response({"error": "Canal não encontrado"}, status=404)
    await ativar_automsg(cid, c, data.get("mensagem",""), s, data.get("banner") or None,
                         resolver_mencao(g, data.get("mencao","")))
    add_log("automsg_criar", f"Canal #{c.name} a cada {data.get('intervalo')}", sess["username"])
    return web.json_response({"ok": True})

async def route_automsg_editar(request):
    sess = await check_auth(request)
    data = await request.json()
    cid = int(data.get("canal_id",0))
    if cid not in auto_msgs: return web.json_response({"error": "Não encontrado"}, status=404)
    if data.get("mensagem"): auto_msgs[cid]["conteudo"] = data["mensagem"]
    if data.get("banner"):   auto_msgs[cid]["banner"]   = data["banner"]
    if data.get("mencao"):   auto_msgs[cid]["mencao"]   = resolver_mencao(auto_msgs[cid]["canal"].guild, data["mencao"])
    if data.get("intervalo"):
        s = parse_intervalo(data["intervalo"])
        if s:
            d = auto_msgs[cid]; d["task"].cancel()
            await ativar_automsg(cid, d["canal"], d["conteudo"], s, d.get("banner"), d.get("mencao",""))
    else:
        canal = auto_msgs[cid]["canal"]
        oid = auto_msgs[cid].get("msg_id")
        if oid:
            try: old = await canal.fetch_message(oid); await old.delete()
            except: pass
        n = await enviar_automsg(canal, auto_msgs[cid]["conteudo"], auto_msgs[cid].get("banner"), auto_msgs[cid].get("mencao",""))
        auto_msgs[cid]["msg_id"] = n.id
    add_log("automsg_editar", f"Canal ID {cid}", sess["username"])
    return web.json_response({"ok": True})

async def route_automsg_parar(request):
    sess = await check_auth(request)
    data = await request.json()
    cid = int(data.get("canal_id",0))
    if cid not in auto_msgs: return web.json_response({"error": "Não encontrado"}, status=404)
    nome = auto_msgs[cid].get("canal_nome","?")
    auto_msgs[cid]["task"].cancel(); del auto_msgs[cid]
    add_log("automsg_parar", f"Canal #{nome}", sess["username"])
    return web.json_response({"ok": True})

async def route_ping(request):
    sess = await check_auth(request)
    data = await request.json()
    g = bot.get_guild(int(data.get("guild_id",0)))
    if not g: return web.json_response({"error": "Servidor não encontrado"}, status=404)
    async def do():
        for c in [c for c in g.text_channels if c.permissions_for(g.me).send_messages]:
            try: msg = await c.send("."); await asyncio.sleep(1); await msg.delete()
            except: pass
    asyncio.ensure_future(do())
    add_log("ping_silencioso", f"Servidor {g.name}", sess["username"])
    return web.json_response({"ok": True})

async def route_editar_msg(request):
    sess = await check_auth(request)
    data = await request.json()
    g = bot.get_guild(int(data.get("guild_id",0)))
    if not g: return web.json_response({"error": "Servidor não encontrado"}, status=404)
    c = g.get_channel(int(data.get("canal_id",0)))
    if not c: return web.json_response({"error": "Canal não encontrado"}, status=404)
    try:
        msg = await c.fetch_message(int(data.get("msg_id",0)))
        if msg.author != bot.user: return web.json_response({"error": "Não posso editar"}, status=403)
        await msg.edit(content=data.get("conteudo",""))
        add_log("editar_msg", f"Msg {data.get('msg_id')} em #{c.name}", sess["username"])
        return web.json_response({"ok": True})
    except Exception as e: return web.json_response({"error": str(e)}, status=500)

async def route_anuncio(request):
    sess = await check_auth(request)
    data = await request.json()
    g = bot.get_guild(int(data.get("guild_id",0)))
    if not g: return web.json_response({"error": "Servidor não encontrado"}, status=404)
    c = g.get_channel(int(data.get("canal_id",0)))
    if not c: return web.json_response({"error": "Canal não encontrado"}, status=404)
    try: cor = int(data.get("cor","5865f2").replace("#",""), 16)
    except: cor = 0x5865f2
    embed = discord.Embed(title=data.get("titulo",""), description=data.get("descricao",""), color=cor)
    if data.get("imagem"): embed.set_image(url=data["imagem"])
    if data.get("thumbnail"): embed.set_thumbnail(url=data["thumbnail"])
    if data.get("rodape"): embed.set_footer(text=data["rodape"])
    mencao = resolver_mencao(g, data.get("mencao",""))
    await c.send(content=mencao or None, embed=embed)
    add_log("anuncio", f"Canal #{c.name}", sess["username"])
    return web.json_response({"ok": True})

async def route_ban(request):
    sess = await check_auth(request)
    data = await request.json()
    g = bot.get_guild(int(data.get("guild_id",0)))
    if not g: return web.json_response({"error": "Servidor não encontrado"}, status=404)
    try:
        uid = int(data.get("user_id",0))
        motivo = data.get("motivo","Banido pelo painel")
        membro = g.get_member(uid) or discord.Object(id=uid)
        await g.ban(membro, reason=motivo, delete_message_days=0)
        add_punicao(uid, "ban", motivo, sess["username"])
        add_log("ban", f"User {uid} — {motivo}", sess["username"])
        return web.json_response({"ok": True})
    except Exception as e: return web.json_response({"error": str(e)}, status=500)

async def route_unban(request):
    sess = await check_auth(request)
    data = await request.json()
    g = bot.get_guild(int(data.get("guild_id",0)))
    if not g: return web.json_response({"error": "Servidor não encontrado"}, status=404)
    try:
        uid = int(data.get("user_id",0))
        await g.unban(discord.Object(id=uid), reason="Desbanido pelo painel")
        add_log("unban", f"User {uid}", sess["username"])
        return web.json_response({"ok": True})
    except Exception as e: return web.json_response({"error": str(e)}, status=500)

async def route_banidos(request):
    await check_auth(request)
    gid = int(request.rel_url.query.get("guild_id",0))
    g = bot.get_guild(gid)
    if not g: return web.json_response({"error": "Servidor não encontrado"}, status=404)
    try:
        banidos = [b async for b in g.bans(limit=100)]
        return web.json_response({"banidos": [
            {"id": str(b.user.id), "nome": str(b.user),
             "avatar": str(b.user.display_avatar.url),
             "motivo": b.reason or "Sem motivo"}
            for b in banidos]})
    except Exception as e: return web.json_response({"error": str(e)}, status=500)

async def route_kick(request):
    sess = await check_auth(request)
    data = await request.json()
    g = bot.get_guild(int(data.get("guild_id",0)))
    if not g: return web.json_response({"error": "Servidor não encontrado"}, status=404)
    try:
        m = g.get_member(int(data.get("user_id",0)))
        if not m: return web.json_response({"error": "Membro não encontrado"}, status=404)
        motivo = data.get("motivo","Kickado pelo painel")
        await m.kick(reason=motivo)
        add_punicao(m.id, "kick", motivo, sess["username"])
        add_log("kick", f"{m} — {motivo}", sess["username"])
        return web.json_response({"ok": True})
    except Exception as e: return web.json_response({"error": str(e)}, status=500)

async def route_timeout(request):
    sess = await check_auth(request)
    data = await request.json()
    g = bot.get_guild(int(data.get("guild_id",0)))
    if not g: return web.json_response({"error": "Servidor não encontrado"}, status=404)
    try:
        m = g.get_member(int(data.get("user_id",0)))
        if not m: return web.json_response({"error": "Membro não encontrado"}, status=404)
        minutos = int(data.get("minutos", 5))
        until = discord.utils.utcnow() + __import__("datetime").timedelta(minutes=minutos)
        motivo = data.get("motivo","Timeout pelo painel")
        await m.timeout(until, reason=motivo)
        add_punicao(m.id, "timeout", f"{minutos}min — {motivo}", sess["username"])
        add_log("timeout", f"{m} por {minutos}min", sess["username"])
        return web.json_response({"ok": True})
    except Exception as e: return web.json_response({"error": str(e)}, status=500)

async def route_punicoes(request):
    await check_auth(request)
    uid = request.rel_url.query.get("user_id","")
    return web.json_response({"punicoes": punicoes.get(uid, [])})

async def route_limpar(request):
    sess = await check_auth(request)
    data = await request.json()
    g = bot.get_guild(int(data.get("guild_id",0)))
    if not g: return web.json_response({"error": "Servidor não encontrado"}, status=404)
    c = g.get_channel(int(data.get("canal_id",0)))
    if not c: return web.json_response({"error": "Canal não encontrado"}, status=404)
    try:
        qtd = min(int(data.get("quantidade",10)), 100)
        deleted = await c.purge(limit=qtd)
        add_log("limpar_msgs", f"{len(deleted)} msgs em #{c.name}", sess["username"])
        return web.json_response({"ok": True, "deletadas": len(deleted)})
    except Exception as e: return web.json_response({"error": str(e)}, status=500)

async def route_slowmode(request):
    sess = await check_auth(request)
    data = await request.json()
    g = bot.get_guild(int(data.get("guild_id",0)))
    if not g: return web.json_response({"error": "Servidor não encontrado"}, status=404)
    c = g.get_channel(int(data.get("canal_id",0)))
    if not c: return web.json_response({"error": "Canal não encontrado"}, status=404)
    try:
        seg = int(data.get("segundos",0))
        await c.edit(slowmode_delay=seg)
        add_log("slowmode", f"#{c.name} → {seg}s", sess["username"])
        return web.json_response({"ok": True})
    except Exception as e: return web.json_response({"error": str(e)}, status=500)

async def route_lockdown(request):
    sess = await check_auth(request)
    data = await request.json()
    g = bot.get_guild(int(data.get("guild_id",0)))
    if not g: return web.json_response({"error": "Servidor não encontrado"}, status=404)
    c = g.get_channel(int(data.get("canal_id",0)))
    if not c: return web.json_response({"error": "Canal não encontrado"}, status=404)
    try:
        travar = data.get("travar", True)
        overwrite = c.overwrites_for(g.default_role)
        overwrite.send_messages = False if travar else None
        await c.set_permissions(g.default_role, overwrite=overwrite)
        add_log("lockdown_on" if travar else "lockdown_off", f"#{c.name}", sess["username"])
        return web.json_response({"ok": True})
    except Exception as e: return web.json_response({"error": str(e)}, status=500)

async def route_dar_cargo(request):
    sess = await check_auth(request)
    data = await request.json()
    g = bot.get_guild(int(data.get("guild_id",0)))
    if not g: return web.json_response({"error": "Servidor não encontrado"}, status=404)
    try:
        m = g.get_member(int(data.get("user_id",0)))
        if not m: return web.json_response({"error": "Membro não encontrado"}, status=404)
        r = g.get_role(int(data.get("cargo_id",0)))
        if not r: return web.json_response({"error": "Cargo não encontrado"}, status=404)
        await m.add_roles(r, reason="Cargo dado pelo painel")
        add_log("dar_cargo", f"{r.name} → {m}", sess["username"])
        return web.json_response({"ok": True})
    except Exception as e: return web.json_response({"error": str(e)}, status=500)

async def route_remover_cargo(request):
    sess = await check_auth(request)
    data = await request.json()
    g = bot.get_guild(int(data.get("guild_id",0)))
    if not g: return web.json_response({"error": "Servidor não encontrado"}, status=404)
    try:
        m = g.get_member(int(data.get("user_id",0)))
        if not m: return web.json_response({"error": "Membro não encontrado"}, status=404)
        r = g.get_role(int(data.get("cargo_id",0)))
        if not r: return web.json_response({"error": "Cargo não encontrado"}, status=404)
        await m.remove_roles(r, reason="Cargo removido pelo painel")
        add_log("remover_cargo", f"{r.name} de {m}", sess["username"])
        return web.json_response({"ok": True})
    except Exception as e: return web.json_response({"error": str(e)}, status=500)

async def route_dm_massa(request):
    sess = await check_auth(request)
    data = await request.json()
    g = bot.get_guild(int(data.get("guild_id",0)))
    if not g: return web.json_response({"error": "Servidor não encontrado"}, status=404)
    msg = data.get("mensagem","")
    if not msg: return web.json_response({"error": "Mensagem vazia"}, status=400)
    async def do():
        for m in g.members:
            if m.bot: continue
            try: await m.send(msg); await asyncio.sleep(1)
            except: pass
    asyncio.ensure_future(do())
    add_log("dm_massa", f"Servidor {g.name}", sess["username"])
    return web.json_response({"ok": True})

async def route_lista_negra_get(request):
    await check_auth(request)
    gid = request.rel_url.query.get("guild_id","")
    return web.json_response({"palavras": lista_negra.get(gid, [])})

async def route_lista_negra_set(request):
    sess = await check_auth(request)
    data = await request.json()
    gid = str(data.get("guild_id",""))
    palavras = data.get("palavras", [])
    lista_negra[gid] = [p.strip().lower() for p in palavras if p.strip()]
    add_log("lista_negra", f"{len(lista_negra[gid])} palavras", sess["username"])
    return web.json_response({"ok": True, "total": len(lista_negra[gid])})

async def route_info_servidor(request):
    await check_auth(request)
    gid = int(request.rel_url.query.get("guild_id",0))
    g = bot.get_guild(gid)
    if not g: return web.json_response({"error": "Servidor não encontrado"}, status=404)
    bots = sum(1 for m in g.members if m.bot)
    online = sum(1 for m in g.members if not m.bot and m.status != discord.Status.offline)
    return web.json_response({
        "nome": g.name, "id": str(g.id), "dono": str(g.owner),
        "membros": g.member_count, "humanos": g.member_count - bots,
        "bots": bots, "online": online,
        "canais_texto": len(g.text_channels), "canais_voz": len(g.voice_channels),
        "cargos": len(g.roles), "criado": g.created_at.strftime("%d/%m/%Y"),
        "icon": str(g.icon.url) if g.icon else None,
        "banner": str(g.banner.url) if g.banner else None,
        "boost_level": g.premium_tier, "boosts": g.premium_subscription_count,
    })

async def route_info_membro(request):
    await check_auth(request)
    gid = int(request.rel_url.query.get("guild_id",0))
    uid = int(request.rel_url.query.get("user_id",0))
    g = bot.get_guild(gid)
    if not g: return web.json_response({"error": "Servidor não encontrado"}, status=404)
    m = g.get_member(uid)
    if not m: return web.json_response({"error": "Membro não encontrado"}, status=404)
    return web.json_response({
        "nome": str(m), "id": str(m.id), "nick": m.nick or "",
        "avatar": str(m.display_avatar.url),
        "entrou": m.joined_at.strftime("%d/%m/%Y %H:%M") if m.joined_at else "?",
        "conta_criada": m.created_at.strftime("%d/%m/%Y"),
        "cargos": [r.name for r in m.roles if r.name != "@everyone"],
        "bot": m.bot, "status": str(m.status),
        "punicoes": punicoes.get(str(uid), [])
    })

async def route_enviar_msg_canal(request):
    sess = await check_auth(request)
    data = await request.json()
    g = bot.get_guild(int(data.get("guild_id",0)))
    if not g: return web.json_response({"error": "Servidor não encontrado"}, status=404)
    c = g.get_channel(int(data.get("canal_id",0)))
    if not c: return web.json_response({"error": "Canal não encontrado"}, status=404)
    mencao = resolver_mencao(g, data.get("mencao",""))
    await c.send(content=(mencao + "\n" if mencao else "") + data.get("mensagem",""))
    add_log("enviar_msg", f"#{c.name}", sess["username"])
    return web.json_response({"ok": True})

async def route_logs(request):
    await check_auth(request)
    return web.json_response({"logs": logs_acoes[:200]})

# ===================== APP =====================

def criar_app():
    app = web.Application(middlewares=[cors_mw])
    app.router.add_route("OPTIONS", "/{p:.*}", lambda r: web.Response())
    app.router.add_post("/api/oauth/callback",      route_oauth)
    app.router.add_get ("/api/me",                  route_me)
    app.router.add_get ("/api/status",              route_status)
    app.router.add_get ("/api/canais",              route_canais)
    app.router.add_get ("/api/membros",             route_membros)
    app.router.add_get ("/api/membros/recentes",    route_membros_recentes)
    app.router.add_get ("/api/membros/online",      route_online)
    app.router.add_get ("/api/cargos",              route_cargos)
    app.router.add_get ("/api/automsg",             route_automsg_list)
    app.router.add_post("/api/automsg/criar",       route_automsg_criar)
    app.router.add_post("/api/automsg/editar",      route_automsg_editar)
    app.router.add_post("/api/automsg/parar",       route_automsg_parar)
    app.router.add_post("/api/ping",                route_ping)
    app.router.add_post("/api/editar_msg",          route_editar_msg)
    app.router.add_post("/api/anuncio",             route_anuncio)
    app.router.add_post("/api/ban",                 route_ban)
    app.router.add_post("/api/unban",               route_unban)
    app.router.add_get ("/api/banidos",             route_banidos)
    app.router.add_post("/api/kick",                route_kick)
    app.router.add_post("/api/timeout",             route_timeout)
    app.router.add_get ("/api/punicoes",            route_punicoes)
    app.router.add_post("/api/limpar",              route_limpar)
    app.router.add_post("/api/slowmode",            route_slowmode)
    app.router.add_post("/api/lockdown",            route_lockdown)
    app.router.add_post("/api/dar_cargo",           route_dar_cargo)
    app.router.add_post("/api/remover_cargo",       route_remover_cargo)
    app.router.add_post("/api/dm_massa",            route_dm_massa)
    app.router.add_get ("/api/lista_negra",         route_lista_negra_get)
    app.router.add_post("/api/lista_negra",         route_lista_negra_set)
    app.router.add_get ("/api/info_servidor",       route_info_servidor)
    app.router.add_get ("/api/info_membro",         route_info_membro)
    app.router.add_post("/api/enviar_msg",          route_enviar_msg_canal)
    app.router.add_get ("/api/logs",                route_logs)
    return app

@bot.event
async def on_ready():
    print(f"✅ Bot: {bot.user} ({bot.user.id})")
    print(f"🌐 API rodando na porta {WEB_PORT}")

async def main():
    app = criar_app()
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", WEB_PORT).start()
    await bot.start(TOKEN)

asyncio.run(main())
