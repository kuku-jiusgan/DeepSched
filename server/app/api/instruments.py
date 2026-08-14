from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from typing import List
from app.core.database import get_db
from app.models import Instrument, InstrumentCapability, MaintenanceWindow, InstrumentFault, User
from app.schemas.schemas import (
    InstrumentCreate, InstrumentOut, CapabilityCreate, CapabilityOut,
    MaintenanceCreate, MaintenanceOut, FaultCreate, FaultOut
)
from app.services.instrument_status_service import list_instruments_with_effective_status
from app.services.instrument_fault_service import (
    InstrumentFaultConflictError,
    InstrumentFaultInvalidError,
    list_faults as list_faults_service,
    list_open_faults as list_open_faults_service,
    report_fault as report_fault_service,
    resolve_fault as resolve_fault_service,
)
from app.api.access import require_management_user
from app.api.users import require_authenticated_user
from app.services.access_control_service import is_management_user

router = APIRouter(prefix="/api/v1/instruments", tags=["instruments"])

@router.post("", response_model=InstrumentOut)
def create_instrument(
    data: InstrumentCreate,
    db: Session = Depends(get_db),
    _user=Depends(require_management_user),
):
    existing = db.query(Instrument).filter(Instrument.code == data.code).first()
    if existing:
        raise HTTPException(status_code=409, detail=f"仪器编码 {data.code} 已存在")
    inst = Instrument(
        code=data.code, name=data.name, instrument_group=data.instrument_group,
        brand=data.brand, model=data.model,
        location=data.location, availability_status=data.availability_status,
        buffer_rate=data.buffer_rate,
        switchover_base_hours=data.switchover_base_hours
    )
    db.add(inst)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail=f"仪器编码 {data.code} 已存在")
    for cap in data.capabilities:
        db.add(InstrumentCapability(instrument_id=inst.id, tag_name=cap.tag_name, tag_value=cap.tag_value))
    db.commit()
    db.refresh(inst)
    return inst

@router.get("", response_model=List[InstrumentOut])
def list_instruments(
    include_unavailable: bool = Query(False),
    db: Session = Depends(get_db),
):
    return list_instruments_with_effective_status(db, include_unavailable=include_unavailable)

@router.get("/faults/open", response_model=List[FaultOut])
def list_open_faults(db: Session = Depends(get_db)):
    return list_open_faults_service(db)

@router.get("/faults", response_model=List[FaultOut])
def list_faults(db: Session = Depends(get_db)):
    return list_faults_service(db)

@router.get("/{inst_id}", response_model=InstrumentOut)
def get_instrument(inst_id: int, db: Session = Depends(get_db)):
    inst = db.query(Instrument).filter(Instrument.id == inst_id).first()
    if not inst:
        raise HTTPException(status_code=404, detail="仪器不存在")
    return inst

@router.put("/{inst_id}", response_model=InstrumentOut)
def update_instrument(
    inst_id: int,
    data: InstrumentCreate,
    db: Session = Depends(get_db),
    _user=Depends(require_management_user),
):
    inst = db.query(Instrument).filter(Instrument.id == inst_id).first()
    if not inst:
        raise HTTPException(status_code=404, detail="仪器不存在")
    existing = db.query(Instrument).filter(Instrument.code == data.code, Instrument.id != inst_id).first()
    if existing:
        raise HTTPException(status_code=409, detail=f"仪器编码 {data.code} 已被其他仪器使用")
    inst.code = data.code
    inst.name = data.name
    inst.instrument_group = data.instrument_group
    inst.brand = data.brand
    inst.model = data.model
    inst.location = data.location
    inst.availability_status = data.availability_status
    inst.buffer_rate = data.buffer_rate
    inst.switchover_base_hours = data.switchover_base_hours
    db.query(InstrumentCapability).filter(InstrumentCapability.instrument_id == inst_id).delete()
    for cap in data.capabilities:
        db.add(InstrumentCapability(instrument_id=inst_id, tag_name=cap.tag_name, tag_value=cap.tag_value))
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail=f"仪器编码 {data.code} 已被其他仪器使用")
    db.refresh(inst)
    return inst

@router.post("/{inst_id}/capabilities", response_model=CapabilityOut)
def add_capability(
    inst_id: int,
    data: CapabilityCreate,
    db: Session = Depends(get_db),
    _user=Depends(require_management_user),
):
    cap = InstrumentCapability(instrument_id=inst_id, tag_name=data.tag_name, tag_value=data.tag_value)
    db.add(cap)
    db.commit()
    db.refresh(cap)
    return cap

@router.post("/{inst_id}/maintenance", response_model=MaintenanceOut)
def add_maintenance(
    inst_id: int,
    data: MaintenanceCreate,
    db: Session = Depends(get_db),
    _user=Depends(require_management_user),
):
    mw = MaintenanceWindow(
        instrument_id=inst_id, start_time=data.start_time,
        end_time=data.end_time, mw_type=data.mw_type, description=data.description
    )
    db.add(mw)
    db.commit()
    db.refresh(mw)
    return mw

@router.get("/{inst_id}/maintenance", response_model=List[MaintenanceOut])
def list_maintenance(inst_id: int, db: Session = Depends(get_db)):
    return db.query(MaintenanceWindow).filter(MaintenanceWindow.instrument_id == inst_id).all()

@router.post("/{inst_id}/fault", response_model=FaultOut)
def report_fault(
    inst_id: int,
    data: FaultCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_authenticated_user),
):
    if data.resolved_at is not None and not is_management_user(user):
        raise HTTPException(status_code=403, detail="只有管理角色可以直接归档故障")
    try:
        fault = report_fault_service(
            db,
            inst_id,
            data.description,
            data.estimated_resolved_at,
            data.resolved_at,
        )
    except InstrumentFaultInvalidError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except InstrumentFaultConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    if not fault:
        raise HTTPException(status_code=404, detail="仪器不存在")
    return fault

@router.delete("/{inst_id}")
def delete_instrument(
    inst_id: int,
    db: Session = Depends(get_db),
    _user=Depends(require_management_user),
):
    inst = db.query(Instrument).filter(Instrument.id == inst_id).first()
    if not inst:
        raise HTTPException(status_code=404, detail="仪器不存在")
    db.delete(inst)
    db.commit()
    return {"status": "deleted"}

@router.put("/{inst_id}/fault/{fault_id}/resolve", response_model=FaultOut)
def resolve_fault(
    inst_id: int,
    fault_id: int,
    db: Session = Depends(get_db),
    _user=Depends(require_management_user),
):
    try:
        fault = resolve_fault_service(db, inst_id, fault_id)
    except InstrumentFaultConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    if not fault:
        raise HTTPException(status_code=404, detail="故障记录不存在")
    return fault
