"""Minimal end-to-end example: 2D steady-state heat transfer.

Starts a COMSOL server, builds a rectangle, assigns a material and two
temperature boundaries, meshes, solves, evaluates the field, exports a PNG,
and saves the model.

    python examples/heat_transfer_2d.py

This bypasses the MCP layer on purpose: it shows the same MPh/Java API calls
that the tools wrap, so you can adapt it into a script or a new tool.
"""

import time
from pathlib import Path

import mph

OUT = Path(__file__).resolve().parent.parent / "out"
OUT.mkdir(exist_ok=True)

t0 = time.time()
client = mph.start(cores=2)
print(f"[{time.time()-t0:.1f}s] server up")

model = client.create("heat_transfer_2d")
java = model.java

# --- Geometry -------------------------------------------------------------
java.modelNode().create("comp1")
java.modelNode("comp1").geom().create("geom1", 2)
geom = java.modelNode("comp1").geom("geom1")
rect = geom.create("r1", "Rectangle")
rect.set("size", ["0.1", "0.2"])
geom.run()

# --- Physics and material -------------------------------------------------
physics = java.modelNode("comp1").physics().create("ht", "HeatTransfer", "geom1")
print("physics:", physics.getType())

material = java.modelNode("comp1").material().create("mat1", "Common")
material.propertyGroup("def").set("thermalconductivity", "400")
material.propertyGroup("def").set("density", "8960")
material.propertyGroup("def").set("heatcapacity", "385")

# bottom boundary at 373.15 K, top boundary at 293.15 K
for tag, boundary, value in (("temp1", 1, "373.15[K]"), ("temp2", 3, "293.15[K]")):
    bc = physics.create(tag, "TemperatureBoundary", 1)
    bc.selection().set([boundary])
    bc.set("T0", value)

# --- Mesh and solve -------------------------------------------------------
java.modelNode("comp1").mesh().create("mesh1")
mesh = java.modelNode("comp1").mesh("mesh1")
mesh.autoMeshSize(4)
mesh.run()
print("elements:", mesh.getNumElem())

java.study().create("std1").create("stat", "Stationary")
print("solving...")
java.study("std1").run()
print(f"[{time.time()-t0:.1f}s] solved")

# --- Results --------------------------------------------------------------
values = model.evaluate("T", unit="degC")
print(f"T range [degC]: {values.min():.2f} .. {values.max():.2f}")

plot = java.result().create("pg1", "PlotGroup2D")
surface = plot.create("surf1", "Surface")
surface.set("expr", "T")
surface.set("unit", "degC")
plot.run()

image = java.result().export().create("img1", "Image")
image.set("plotgroup", "pg1")
image.set("pngfilename", str(OUT / "heat_transfer_2d.png"))
image.set("width", "900")
image.set("height", "650")
image.run()
print("image:", OUT / "heat_transfer_2d.png")

model.save(OUT / "heat_transfer_2d.mph")
print("model:", OUT / "heat_transfer_2d.mph")
