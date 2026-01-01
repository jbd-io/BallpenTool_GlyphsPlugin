# encoding: utf-8
###########################################################################################################
#
# BallPen Tool Plugin — v1.0
#
###########################################################################################################
from __future__ import division, print_function, unicode_literals
import objc, os, math
from GlyphsApp import Glyphs, GSPath, GSNode, GSOFFCURVE, GSCURVE, GSLINE, UPDATEINTERFACE
from GlyphsApp.plugins import SelectTool, PalettePlugin
from AppKit import NSImage, NSColor, NSBezierPath, NSPoint
# ----------------------------------------------------------
# Constantes globales
# ----------------------------------------------------------
DEFAULT_SIMPLIFY_EPSILON = 2.0
DEFAULT_STROKE_WIDTH = 20.0
MIN_DISTANCE = 4.0
# ----------------------------------------------------------
# Fonctions utilitaires
# ----------------------------------------------------------
def distance(p1, p2):
    return math.hypot(p2.x - p1.x, p2.y - p1.y)
def distance_point_segment(p, a, b):
    x, y = p.x, p.y
    x1, y1 = a.x, a.y
    x2, y2 = b.x, b.y
    dx = x2 - x1
    dy = y2 - y1
    if dx == 0 and dy == 0:
        return math.hypot(x - x1, y - y1)
    t = ((x - x1) * dx + (y - y1) * dy) / (dx * dx + dy * dy)
    t = max(0.0, min(1.0, t))
    projx = x1 + t * dx
    projy = y1 + t * dy
    return math.hypot(x - projx, y - projy)
def rdp_simplify(points, epsilon):
    if len(points) < 3:
        return points[:]
    dmax = 0.0
    index = 0
    a = points[0]
    b = points[-1]
    for i in range(1, len(points) - 1):
        d = distance_point_segment(points[i], a, b)
        if d > dmax:
            index = i
            dmax = d
    if dmax > epsilon:
        left = rdp_simplify(points[: index + 1], epsilon)
        right = rdp_simplify(points[index:], epsilon)
        return left[:-1] + right
    else:
        return [points[0], points[-1]]
def ns_add(a, b): return NSPoint(a.x + b.x, a.y + b.y)
def ns_sub(a, b): return NSPoint(a.x - b.x, a.y - b.y)
def ns_mul(a, s): return NSPoint(a.x * s, a.y * s)
def ns_div(a, s): return NSPoint(a.x / s, a.y / s)

# Nouvelle fonction pour la symétrie des tangentes
def make_tangent_symmetric(p_anchor, p_control_out):
    """Calcule le point de contrôle entrant symétrique par rapport au point d'ancrage."""
    return ns_sub(ns_mul(p_anchor, 2.0), p_control_out)

# ----------------------------------------------------------
# B-spline -> Bézier
# ----------------------------------------------------------
def b_spline_to_bezier(points):
    n = len(points)
    if n < 2:
        return []
    if n == 2:
        p0, p1 = points
        c1 = NSPoint(p0.x + (p1.x - p0.x) / 3, p0.y + (p1.y - p0.y) / 3)
        c2 = NSPoint(p0.x + 2 * (p1.x - p0.x) / 3, p0.y + 2 * (p1.y - p0.y) / 3)
        return [(p0, c1, c2, p1)]
    padded = [points[0], points[0]] + points[:] + [points[-1], points[-1]]
    beziers = []
    for i in range(len(padded) - 3):
        P0, P1, P2, P3 = padded[i], padded[i + 1], padded[i + 2], padded[i + 3]
        Q0 = ns_div(ns_add(ns_add(P0, ns_mul(P1, 4.0)), P2), 6.0)
        Q1 = ns_div(ns_add(ns_mul(P1, 4.0), ns_mul(P2, 2.0)), 6.0)
        Q2 = ns_div(ns_add(ns_mul(P1, 2.0), ns_mul(P2, 4.0)), 6.0)
        Q3 = ns_div(ns_add(ns_add(P1, ns_mul(P2, 4.0)), P3), 6.0)
        beziers.append((Q0, Q1, Q2, Q3))
    return [seg for seg in beziers if abs(seg[0].x - seg[3].x) > 1e-6 or abs(seg[0].y - seg[3].y) > 1e-6]
# ----------------------------------------------------------
# Fonctions utilitaires supplémentaires
# ----------------------------------------------------------
def trim_ends(points, trim_length=2.0):
    """
    Raccourcit légèrement les extrémités du tracé pour éviter les surlongueurs de tangente
    et les doublons de points.
    """
    if len(points) < 3:
        return points[:]
    def trim_segment(points, trim_length, from_start=True):
        d = 0
        if from_start:
            for i in range(len(points) - 1):
                seg_len = distance(points[i], points[i + 1])
                if d + seg_len >= trim_length:
                    ratio = (trim_length - d) / seg_len
                    new_p = NSPoint(
                        points[i].x + (points[i + 1].x - points[i].x) * ratio,
                        points[i].y + (points[i + 1].y - points[i].y) * ratio
                    )
                    return [new_p] + points[i + 1:]
                d += seg_len
            return points
        else:
            for i in range(len(points) - 1, 0, -1):
                seg_len = distance(points[i], points[i - 1])
                if d + seg_len >= trim_length:
                    ratio = (trim_length - d) / seg_len
                    new_p = NSPoint(
                        points[i].x + (points[i - 1].x - points[i].x) * ratio,
                        points[i].y + (points[i - 1].y - points[i].y) * ratio
                    )
                    return points[:i] + [new_p]
                d += seg_len
            return points
    # Ajustement : Augmentation du facteur de trim pour être plus efficace.
    trim = trim_length * 4.0
    trimmed = trim_segment(points, trim, from_start=True)
    trimmed = trim_segment(trimmed, trim, from_start=False)
    # Supprime tout doublon exact en fin ou début
    if distance(trimmed[0], trimmed[1]) < 0.1 and len(trimmed) > 2:
        trimmed = trimmed[1:]
    if distance(trimmed[-1], trimmed[-2]) < 0.1 and len(trimmed) > 2:
        trimmed = trimmed[:-1]
    return trimmed
def clamp_tangents(beziers):
    """
    Empêche les tangentes du début et de la fin d'aller au-delà des points extrêmes.
    Ajustement par compression pour ancrer les extrémités.
    """
    if not beziers:
        return beziers
    
    # Ajustement début : Compression du premier point de contrôle c1 vers p0
    p0_first, c1_first, c2_first, p1_first = beziers[0]
    COMPRESSION_FACTOR = 0.85 
    
    # Nouvelle position de c1: p0 + (c1 - p0) * facteur
    c1_first_new = ns_add(p0_first, ns_mul(ns_sub(c1_first, p0_first), COMPRESSION_FACTOR))
    beziers[0] = (p0_first, c1_first_new, c2_first, p1_first)

    # Ajustement fin : Compression du dernier point de contrôle c2 vers p1
    p0_last, c1_last, c2_last, p1_last = beziers[-1]
    
    # Nouvelle position de c2: p1 + (c2 - p1) * facteur (le vecteur est inversé)
    c2_last_new = ns_add(p1_last, ns_mul(ns_sub(c2_last, p1_last), COMPRESSION_FACTOR))
    
    beziers[-1] = (p0_last, c1_last, c2_last_new, p1_last)
    
    return beziers
# ----------------------------------------------------------
# Palette intégrée : ToolVariables
# ----------------------------------------------------------
class BallPenToolVariables(PalettePlugin):
    dialog = objc.IBOutlet()
    thicknessSlider = objc.IBOutlet()
    smoothingSlider = objc.IBOutlet()
    thicknessLabel = objc.IBOutlet()
    smoothingLabel = objc.IBOutlet()
    thickness = 20.0
    smoothing = 6
    @objc.python_method
    def settings(self):
        self.name = Glyphs.localize({
            'en': 'Ballpen settings',
            'fr': 'Paramètres du stylo',
            'de': 'Kugelschreiber-Einstellungen',
            'es': 'Ajustes del boli',
            'zh': '笔设置',
            'ja': 'ペンの設定',
            'pt': 'Configurações da caneta',
            'it': 'Impostazioni della penna',
            'nl': 'Peninstellingen',
            'ko': '펜 설정',
            'ru': 'Настройки пера',
        })
        self.loadNib('IBdialog', __file__)
        self.dialog.setController_(self)
    @objc.python_method
    def start(self):
        Glyphs.addCallback(self.update, UPDATEINTERFACE)
    @objc.python_method
    def __del__(self):
        Glyphs.removeCallback(self.update)
    def minHeight(self): return 120
    def maxHeight(self): return 120
    @objc.IBAction
    def thicknessChanged_(self, sender):
        self.thickness = round(sender.floatValue())
        if BallPen.instance:
            BallPen.instance.strokeWidth = self.thickness
        self.update(None)
    @objc.IBAction
    def smoothingChanged_(self, sender):
        self.smoothing = round(sender.floatValue())
        if BallPen.instance:
            BallPen.instance.simplifyEpsilon = DEFAULT_SIMPLIFY_EPSILON * (1.25 ** self.smoothing)
        self.update(None)
    @objc.python_method
    def update(self, sender):
        labels = Glyphs.localize({
            'en': {'thickness_label': 'Thickness:', 'smoothing_label': 'Smoothing:'},
            'fr': {'thickness_label': 'Épaisseur :', 'smoothing_label': 'Lissage :'},
            'de': {'thickness_label': 'Dicke:', 'smoothing_label': 'Glättung:'},
            'es': {'thickness_label': 'Grosor:', 'smoothing_label': 'Suavizado:'},
            'zh': {'thickness_label': '粗细:', 'smoothing_label': '平滑度:'},
            'ja': {'thickness_label': '太さ:', 'smoothing_label': 'スムージング:'},
            'pt': {'thickness_label': 'Espessura:', 'smoothing_label': 'Suavização:'},
            'it': {'thickness_label': 'Spessore:', 'smoothing_label': 'Levigatura:'},
            'nl': {'thickness_label': 'Dikte:', 'smoothing_label': 'Gladmaken:'},
            'ko': {'thickness_label': '두께:', 'smoothing_label': '매끄럽게:'},
            'ru': {'thickness_label': 'Толщина:', 'smoothing_label': 'Сглаживание:'},
        })
        if self.thicknessLabel:
            self.thicknessLabel.setStringValue_(f'{labels["thickness_label"]} {int(self.thickness)}')
        if self.smoothingLabel:
            self.smoothingLabel.setStringValue_(f'{labels["smoothing_label"]} {int(self.smoothing)}')
    @objc.python_method
    def __file__(self):
        return __file__
# ----------------------------------------------------------
# BallPen Tool principal
# ----------------------------------------------------------
class BallPen(SelectTool):
    instance = None
    @objc.python_method
    def settings(self):
        self.name = Glyphs.localize({
            'en': 'Ballpen',
            'fr': 'Stylo',
            'de': 'Kugelschreiber',
            'es': 'Bolígrafo',
            'zh': '圆珠笔',
            'ja': 'ボールペン',
            'pt': 'Caneta',
            'it': 'Penna',
            'nl': 'Balpen',
            'ko': '볼펜',
            'ru': 'Шариковая ручка',
        })
        icon_path = os.path.join(os.path.dirname(__file__), "BallPenTool.pdf")
        highlight_path = os.path.join(os.path.dirname(__file__), "BallPenToolHighlight.pdf")
        self.default_image = NSImage.alloc().initByReferencingFile_(icon_path)
        self.active_image = NSImage.alloc().initByReferencingFile_(highlight_path)
        self.tool_bar_image = self.default_image
        self.toolbarIconName = "BallPenTool"
        self.keyboardShortcut = 'Y'
        self.toolbarPosition = 182
        self.strokeWidth = DEFAULT_STROKE_WIDTH
        self.simplifyEpsilon = DEFAULT_SIMPLIFY_EPSILON
        self.minDistance = MIN_DISTANCE
        self.roundCaps = True
        BallPen.instance = self
    @objc.python_method
    def start(self):
        self.points = []
        self.lastPoint = None
    def mouseDown_(self, theEvent):
        view = self.editViewController().graphicView()
        loc = view.getActiveLocation_(theEvent)
        self.points = [loc]
        self.lastPoint = loc
        view.setNeedsDisplay_(True)
    def mouseDragged_(self, theEvent):
        if not self.lastPoint:
            return
        view = self.editViewController().graphicView()
        loc = view.getActiveLocation_(theEvent)
        if distance(self.lastPoint, loc) >= self.minDistance:
            self.points.append(loc)
            self.lastPoint = loc
            view.setNeedsDisplay_(True)
    def mouseUp_(self, theEvent):
        objc.super(BallPen, self).mouseUp_(theEvent)
        view = self.editViewController().graphicView()
        if len(self.points) < 2:
            self.points = []
            self.lastPoint = None
            view.setNeedsDisplay_(True)
            return
        layer = view.activeLayer()
        path = GSPath()
        path.closed = False
        simplified_points = rdp_simplify(self.points, self.simplifyEpsilon)
        if len(simplified_points) < 2:
            simplified_points = self.points[:]
        # Trim et Bézier
        simplified_points = trim_ends(simplified_points, trim_length=self.strokeWidth * 0.05)
        beziers = b_spline_to_bezier(simplified_points)
        beziers = clamp_tangents(beziers)
        if not beziers:
            for pt in simplified_points:
                path.nodes.append(GSNode(NSPoint(round(pt.x), round(pt.y)), type=GSLINE))
            layer.paths.append(path)
            self.points = []
            self.lastPoint = None
            view.setNeedsDisplay_(True)
            return
        
        first = True
        previous_c2 = None
        
        for i, (p0, c1, c2, p1) in enumerate(beziers):
            # Arrondi pour les points d'ancrage (on-curve)
            p0_rounded = NSPoint(round(p0.x), round(p0.y))
            p1_rounded = NSPoint(round(p1.x), round(p1.y))

            if first:
                # Segment de début: Pas de symétrie nécessaire
                path.nodes.append(GSNode(p0_rounded, type=GSLINE))
                path.nodes[-1].smooth = True 
                
                path.nodes.append(GSNode(c1, type=GSOFFCURVE))
                path.nodes.append(GSNode(c2, type=GSOFFCURVE))
                path.nodes.append(GSNode(p1_rounded, type=GSCURVE))
                path.nodes[-1].smooth = True
                
                previous_c2 = c2 # C2 du premier segment (flottant)
                first = False
            else:
                # Segments intermédiaires: Forcer c1 à être symétrique par rapport à l'ancien c2
                # Le point d'ancrage commun est p0_rounded (qui est l'arrondi du p1 précédent)
                c1_new = make_tangent_symmetric(p0_rounded, previous_c2) 
                
                # Ajout des nœuds avec la tangente c1 ajustée
                path.nodes.append(GSNode(c1_new, type=GSOFFCURVE))
                path.nodes.append(GSNode(c2, type=GSOFFCURVE))
                path.nodes.append(GSNode(p1_rounded, type=GSCURVE))
                path.nodes[-1].smooth = True
                
                previous_c2 = c2 # Mettre à jour pour la prochaine itération (flottant)
        
        try:
            path.attributes["strokeWidth"] = self.strokeWidth
            path.attributes["lineCapStart"] = 1
            path.attributes["lineCapEnd"] = 1
        except:
            pass
        layer.paths.append(path)
        self.points = []
        self.lastPoint = None
        view.setNeedsDisplay_(True)
    @objc.python_method
    def background(self, layer):
        simplified_points = rdp_simplify(self.points, self.simplifyEpsilon)
        if len(simplified_points) < 2:
            return
        color = NSColor.blackColor().colorWithAlphaComponent_(0.5)
        color.set()
        bezier = NSBezierPath.bezierPath()
        bezier.setLineWidth_(self.strokeWidth)
        bezier.setLineCapStyle_(1)
        beziers = b_spline_to_bezier(simplified_points)
        if not beziers:
            return
        bezier.moveToPoint_(beziers[0][0])
        for p0, c1, c2, p1 in beziers:
            bezier.curveToPoint_controlPoint1_controlPoint2_(p1, c1, c2)
        bezier.stroke()
    @objc.python_method
    def __file__(self):
        return __file__