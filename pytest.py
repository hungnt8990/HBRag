
import arcpy

ImportedP = arcpy.GetParameterAstext(0)
fields = arcpy.ListFields(ImportedP)

fieldList = []
for field in fields:
    fieldList.append(field.name)

arcpy.AddMessage(f"Field list: {fieldList}")


del fieldList[0:2]
arcpy.AddMessage(f"Field list del: {fieldList}")

for index,field in enumerate(fieldList):
    arcpy.AddMessage(f"Field list del: {index} - {field}")